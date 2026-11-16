"""Copies the on-prem backup keys of cloud apps into Vault, then turns backups on."""

import asyncio
import base64
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.models.environment import AppEnvironment
from app.models.project import TargetCloud
from app.services import app_repo
from app.services import app_security_data as security_data
from app.services import vault_client
from app.services.clusters.state import project_cloud
from app.services.environments import gitops
from app.services.environments.layout import ENVIRONMENTS, application
from app.services.github_service import (
    get_file_content,
    get_org_installation_token,
    put_file_content,
)
from app.services.kube_client import kube_get
from app.services.security.collector import K8S_TEMPLATE

logger = logging.getLogger(__name__)

# Matches the VSO reconciliationTime (Cloud/terraform/public/*/platform/vso-values.yaml).
TICK_SECONDS = 60.0
COMMIT_MESSAGE = "chore(cmp): enable pre-migration backups (backup key ready)"
KEY_FIELDS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


@dataclass(frozen=True)
class BackupTarget:
    project: str
    app: str
    environment: str
    secret_namespace: str
    secret_name: str
    vault_path: str


def db_base_name(release: str, environment: str) -> str:
    """The CNPG cluster name the app chart derives from the release name."""
    if environment == "staging":
        base = release.removesuffix("-stg")[:43].removesuffix("-") + "-stg"
    else:
        base = release[:47].removesuffix("-")
    return base + "-db"


def targets_for(
    project: str, app: str, environments: list[str]
) -> list[BackupTarget]:
    return [
        BackupTarget(
            project=project,
            app=app,
            environment=env,
            secret_namespace=f"{project}-backups",
            secret_name=db_base_name(
                application(project, app, "backend", env), env
            )
            + "-backup",
            vault_path=(
                f"{app}/backup" if env == "prod" else f"{app}/staging/backup"
            ),
        )
        for env in environments
    ]


def _read_key(target: BackupTarget) -> dict[str, str] | None:
    secret = kube_get(
        f"/api/v1/namespaces/{target.secret_namespace}"
        f"/secrets/{target.secret_name}"
    )
    data = (secret or {}).get("data") or {}
    if not all(data.get(field) for field in KEY_FIELDS):
        return None
    return {
        field: base64.b64decode(data[field]).decode() for field in KEY_FIELDS
    }


async def _enable_backup_before(deployment: Deployment) -> None:
    """Commit migrations.backupBefore: true when the file says false."""
    repo = app_repo.bound_repo(
        deployment.github_repo_url, deployment.name, deployment.status
    )
    app_type = security_data.app_type_of(
        deployment.app_config, deployment.terraform_outputs
    )
    path = security_data.values_files_for(app_type)[1]
    token = await get_org_installation_token()
    content, sha = await get_file_content(token, repo, path)
    values = gitops.load(content)
    migrations = values.get("migrations")
    if (
        not isinstance(migrations, dict)
        or migrations.get("backupBefore") is not False
    ):
        return
    migrations["backupBefore"] = True
    await put_file_content(
        installation_token=token,
        repo_full_name=repo,
        file_path=path,
        content=gitops.dump(values),
        message=COMMIT_MESSAGE,
        sha=sha,
    )


async def _sync_deployment(db: Session, deployment: Deployment) -> list[str]:
    project, app = deployment.project_id, deployment.name
    staging = (
        db.query(AppEnvironment)
        .filter_by(deployment_id=deployment.id, name="staging", state="ready")
        .first()
    )
    environments = [e for e in ENVIRONMENTS if e == "prod" or staging]
    written = []
    prod_ready = False
    for target in targets_for(project, app, environments):
        key = _read_key(target)
        if key is None:
            continue
        mount = f"project-{project}"
        if await vault_client.read_kv(mount, target.vault_path) != key:
            await vault_client.write_kv(mount, target.vault_path, key)
            written.append(target.vault_path)
        prod_ready = prod_ready or target.environment == "prod"
    if prod_ready:
        await _enable_backup_before(deployment)
    return written


async def sync_keys(db: Session) -> list[str]:
    """Returns the Vault paths written; a failing app does not stop the others."""
    written: list[str] = []
    deployments = (
        db.query(Deployment)
        .filter(
            Deployment.template_id == K8S_TEMPLATE,
            Deployment.status == DeploymentStatus.RUNNING,
        )
        .all()
    )
    for deployment in deployments:
        app_type = security_data.app_type_of(
            deployment.app_config, deployment.terraform_outputs
        )
        cloud = project_cloud(db, deployment.project_id)
        if app_type != "fullstack" or cloud == TargetCloud.ONPREM:
            continue
        try:
            written += await _sync_deployment(db, deployment)
        except Exception:  # one bad app must not block the others
            logger.exception("backup key sync failed for %s", deployment.name)
    return written


async def cloud_backups_loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                await sync_keys(db)
        except Exception:  # the loop must survive one bad tick
            logger.exception("cloud backups tick failed")
        await asyncio.sleep(TICK_SECONDS)
