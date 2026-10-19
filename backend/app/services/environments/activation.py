"""
Turning an app's staging environment on and off.

On: Terraform creates what staging must not share with prod (OIDC client,
secrets and a Vault role limited to them, CNAME), one commit writes
deploy/env/staging and deploy/env/prod, then the registry record lists
staging, which makes Argo CD create the namespace and Applications.
Off: the reverse, with prod's image written back into the shared files first.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.environment import AppEnvironment, Promotion, VerificationRun
from app.services import project_registry
from app.services.environments import gitops, layout
from app.services.template_repository import get_repository
from app.services.terraform_executor import create_executor

logger = logging.getLogger(__name__)

STAGING = "staging"
_tasks: set[asyncio.Task] = set()


class ActivationRefused(Exception):
    """Staging cannot be turned on or off now; the message says why."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def _executor(deployment: Deployment):
    module = get_repository().get_template_path("k3s-gitops-app")
    if module is None:
        raise ActivationRefused(
            "The k3s-gitops-app template is not available."
        )
    return create_executor(
        Path(module) / "staging",
        f"{deployment.name}-staging",
        f"cnp/projects/{deployment.project_id}/{deployment.name}/staging",
        log_file=Path("logs/deployments") / f"{deployment.id}-staging.log",
    )


def _variables(deployment: Deployment) -> dict[str, Any]:
    config = json.loads(deployment.app_config or "{}")
    variables = {
        "app_name": deployment.name,
        "project_name": deployment.project_id,
        "target_cloud": config.get("target_cloud", "onprem"),
    }
    if config.get("keycloak_realm_id"):
        variables["keycloak_realm_id"] = config["keycloak_realm_id"]
    return variables


def _set(
    db: Session, row: AppEnvironment, state: str, detail: str | None = None
) -> None:
    row.state = state
    row.detail = detail
    row.updated_at = now()
    db.commit()


def check_can_enable(db: Session, deployment: Deployment) -> None:
    config = json.loads(deployment.app_config or "{}")
    if config.get("target_cloud", "onprem") != "onprem":
        raise ActivationRefused(
            "Staging environments are only available on-prem for now."
        )
    row = db.get(AppEnvironment, (deployment.id, STAGING))
    if row is not None and row.state != "failed":
        raise ActivationRefused(f"Staging is already {row.state}.")


def check_can_disable(db: Session, deployment: Deployment) -> AppEnvironment:
    row = db.get(AppEnvironment, (deployment.id, STAGING))
    if row is None or row.state not in ("ready", "failed"):
        raise ActivationRefused(
            "This app has no staging environment to turn off."
        )
    if (
        db.query(Promotion)
        .filter_by(deployment_id=deployment.id)
        .filter(Promotion.status.in_(("running", "waiting_approval")))
        .first()
    ):
        raise ActivationRefused(
            "A promotion is in progress: finish or cancel it first."
        )
    if (
        db.query(VerificationRun)
        .filter_by(
            deployment_id=deployment.id, environment=STAGING, status="running"
        )
        .first()
    ):
        raise ActivationRefused(
            "A verification is running on staging: wait for it or cancel it."
        )
    return row


def start_enable(
    db: Session, deployment: Deployment, offhours: dict[str, Any]
) -> AppEnvironment:
    check_can_enable(db, deployment)
    row = db.get(AppEnvironment, (deployment.id, STAGING))
    if row is None:
        row = AppEnvironment(
            deployment_id=deployment.id,
            name=STAGING,
            state="provisioning",
            updated_at=now(),
        )
        db.add(row)
    _set(
        db,
        row,
        "provisioning",
        "Creating the OIDC client, secrets and hostname",
    )
    _spawn(_enable(deployment.id, offhours))
    return row


def start_disable(db: Session, deployment: Deployment) -> AppEnvironment:
    row = check_can_disable(db, deployment)
    _set(db, row, "removing", "Writing prod's image back to the shared files")
    _spawn(_disable(deployment.id))
    return row


def _spawn(coroutine) -> None:
    task = asyncio.create_task(coroutine)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _enable(deployment_id: int, offhours: dict[str, Any]) -> None:
    with SessionLocal() as db:
        deployment = db.get(Deployment, deployment_id)
        row = db.get(AppEnvironment, (deployment_id, STAGING))
        try:
            executor = _executor(deployment)
            await asyncio.to_thread(executor.init)
            outputs = await asyncio.to_thread(
                executor.apply, _variables(deployment)
            )
            row.hostname = outputs["hostname"]
            _set(
                db,
                row,
                "provisioning",
                "Writing deploy/env/staging and deploy/env/prod",
            )
            await _write_env_files(deployment, outputs, offhours)
            _set(
                db,
                row,
                "provisioning",
                "Adding staging to the registry record",
            )
            await project_registry.set_app_environments(
                deployment.project_id,
                deployment.name,
                ["prod", STAGING],
                row.hostname,
            )
            _set(db, row, "ready", None)
        except Exception as exc:  # whatever broke, the row must say so
            logger.exception(
                "enabling staging for deployment %s failed", deployment_id
            )
            _set(db, row, "failed", f"Turning staging on failed: {exc}")


async def _write_env_files(
    deployment: Deployment, outputs: dict[str, Any], offhours: dict[str, Any]
) -> None:
    kind = gitops.app_type(deployment)
    token = await gitops.token_for(deployment)
    repo = gitops.repo_of(deployment)

    async def build(head: str) -> dict[str, str]:
        base = await gitops.read_base(token, repo, kind, head)
        files = {}
        for component, values in base.items():
            image = layout.image_of(values)
            # Prod keeps running exactly what it runs: same repository and tag.
            files[layout.env_values_file("prod", component)] = gitops.dump(
                {"image": image}
            )
            files[layout.env_values_file(STAGING, component)] = gitops.dump(
                layout.staging_values(
                    image,
                    deployment.project_id,
                    deployment.name,
                    outputs["hostname"],
                    outputs["vault_path"],
                    outputs["vault_role"],
                    outputs["oidc_client_id"],
                    offhours,
                    exposed=bool((values.get("ingress") or {}).get("enabled")),
                    has_auth="auth" in values,
                )
            )
        return files

    await gitops.commit(
        token,
        repo,
        build,
        f"chore(env): add the staging environment of {deployment.name} via CMP\n\n"
        "Release candidates (vX.Y.Z-rc.N) now go to staging; prod changes through "
        "a promotion from the CMP.",
    )


async def _disable(deployment_id: int) -> None:
    with SessionLocal() as db:
        deployment = db.get(Deployment, deployment_id)
        row = db.get(AppEnvironment, (deployment_id, STAGING))
        try:
            await _restore_shared_files(deployment)
            _set(
                db,
                row,
                "removing",
                "Removing staging from the registry record",
            )
            await project_registry.set_app_environments(
                deployment.project_id, deployment.name, ["prod"], None
            )
            _set(
                db,
                row,
                "removing",
                "Deleting the OIDC client, secrets and hostname",
            )
            executor = _executor(deployment)
            await asyncio.to_thread(executor.init)
            await asyncio.to_thread(executor.destroy, _variables(deployment))
            db.delete(row)
            db.commit()
        except Exception as exc:  # whatever broke, the row must say so
            logger.exception(
                "disabling staging for deployment %s failed", deployment_id
            )
            _set(db, row, "failed", f"Turning staging off failed: {exc}")


async def _restore_shared_files(deployment: Deployment) -> None:
    """
    Put prod's image back where the prod image updater writes, and drop
    deploy/env/: without staging, the Applications read only the shared files.
    """
    kind = gitops.app_type(deployment)
    token = await gitops.token_for(deployment)
    repo = gitops.repo_of(deployment)

    async def build(head: str) -> dict[str, str | None]:
        base = await gitops.read_base(token, repo, kind, head)
        files: dict[str, str | None] = {}
        for component, values in base.items():
            prod = await gitops.read_values(
                token, repo, layout.env_values_file("prod", component), head
            )
            if prod and prod.get("image"):
                # The pinned tag as is: prod keeps the exact digest it runs. The
                # prod updater takes over from there with the next final vX.Y.Z
                # (lab L-P1: it must not pick an older final over an rc).
                values.setdefault("image", {})["repository"] = prod[
                    "image"
                ].get("repository")
                values["image"]["tag"] = prod["image"].get("tag")
                files[layout.base_values_file(kind, component)] = gitops.dump(
                    values
                )
            for environment in ("prod", STAGING):
                path = layout.env_values_file(environment, component)
                if (
                    await gitops.read_values(token, repo, path, head)
                    is not None
                ):
                    files[path] = None
        return files

    await gitops.commit(
        token,
        repo,
        build,
        f"chore(env): remove the staging environment of {deployment.name} via CMP",
    )


async def set_offhours(
    deployment: Deployment, offhours: dict[str, Any]
) -> str:
    """Commit the staging offhours settings of every component."""
    kind = gitops.app_type(deployment)
    token = await gitops.token_for(deployment)
    repo = gitops.repo_of(deployment)

    async def build(head: str) -> dict[str, str]:
        files = {}
        for component in layout.components(kind):
            path = layout.env_values_file(STAGING, component)
            values = await gitops.read_values(token, repo, path, head)
            if values is None:
                raise ActivationRefused(f"{path} is missing: is staging on?")
            if dict(values.get("offhours") or {}) == offhours:
                continue
            values["offhours"] = dict(offhours)
            files[path] = gitops.dump(values)
        return files

    state = "on" if offhours.get("enabled") else "off"
    return await gitops.commit(
        token,
        repo,
        build,
        f"chore(env): staging offhours {state} for {deployment.name} via CMP",
    )


async def get_offhours(deployment: Deployment) -> dict[str, Any]:
    """The staging offhours settings, as committed for the first component."""
    kind = gitops.app_type(deployment)
    token = await gitops.token_for(deployment)
    path = layout.env_values_file(STAGING, layout.components(kind)[0])
    values = await gitops.read_values(
        token, gitops.repo_of(deployment), path, "main"
    )
    if values is None:
        raise ActivationRefused(f"{path} is missing: is staging on?")
    return {
        **layout.DEFAULT_STAGING_OFFHOURS,
        **dict(values.get("offhours") or {}),
    }


def destroy_with_app(db: Session, deployment: Deployment) -> None:
    """
    Delete the staging environment's Terraform resources of an app being
    deleted, before the app's own (they rely on the project tunnel).
    Synchronous, like the deletion task that calls it.
    """
    row = db.get(AppEnvironment, (deployment.id, STAGING))
    if row is None:
        return
    executor = _executor(deployment)
    executor.init()
    executor.destroy(_variables(deployment))
    db.delete(row)
    db.commit()
