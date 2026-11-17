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
from app.services.kube_client import (
    ClusterNotConnectedError,
    kube_get,
    kube_list,
)
from app.services.security.collector import K8S_TEMPLATE

logger = logging.getLogger(__name__)

# Matches the VSO reconciliationTime (Cloud/terraform/public/*/*/platform/vso-values.yaml).
TICK_SECONDS = 60.0
COMMIT_MESSAGE = "chore(cmp): enable pre-migration backups (backup key ready)"
KEY_FIELDS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")

# Deployments whose backupBefore check is done, so the repo is not read every tick.
_settled: set[int] = set()
# Last error per deployment: a permanent failure is logged once, not every tick.
_last_error: dict[int, str] = {}


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


def _archiving(target: BackupTarget, cloud: TargetCloud) -> bool:
    # Any Cluster of the namespace: a restore renames it to <name>-r<N>.
    clusters = kube_list(
        f"/apis/postgresql.cnpg.io/v1/namespaces/{target.project}-{target.app}"
        "/clusters",
        cloud=cloud,
    )
    return any(
        c.get("type") == "ContinuousArchiving" and c.get("status") == "True"
        for cluster in clusters
        for c in (cluster.get("status") or {}).get("conditions") or []
    )


async def _enable_backup_before(deployment: Deployment) -> None:
    """Commit migrations.backupBefore: true when the file says false."""
    repo = app_repo.bound_repo(
        deployment.github_repo_url,
        deployment.name,
        deployment.status,
        deployment.template_id,
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


async def _sync_deployment(
    db: Session, deployment: Deployment, cloud: TargetCloud
) -> list[str]:
    project, app = deployment.project_id, deployment.name
    staging = (
        db.query(AppEnvironment)
        .filter_by(deployment_id=deployment.id, name="staging", state="ready")
        .first()
    )
    environments = [e for e in ENVIRONMENTS if e == "prod" or staging]
    written = []
    prod_target = None
    for target in targets_for(project, app, environments):
        key = await asyncio.to_thread(_read_key, target)
        if key is None:
            continue
        mount = f"project-{project}"
        if await vault_client.read_kv(mount, target.vault_path) != key:
            await vault_client.write_kv(mount, target.vault_path, key)
            written.append(target.vault_path)
        elif target.environment == "prod":
            prod_target = target  # already in Vault before this tick
    if prod_target and deployment.id not in _settled:
        try:
            # Archiving works only if Secret, flow and bucket all do: VSO delivery time is unbounded.
            ready = await asyncio.to_thread(_archiving, prod_target, cloud)
        except ClusterNotConnectedError:
            return written
        if ready:
            await _enable_backup_before(deployment)
            _settled.add(deployment.id)
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
        try:
            app_type = security_data.app_type_of(
                deployment.app_config, deployment.terraform_outputs
            )
            cloud = project_cloud(db, deployment.project_id)
            if app_type != "fullstack" or cloud == TargetCloud.ONPREM:
                continue
            written += await _sync_deployment(db, deployment, cloud)
            _last_error.pop(deployment.id, None)
        except Exception as exc:  # one bad app must not block the others
            db.rollback()
            if _last_error.get(deployment.id) != repr(exc):
                _last_error[deployment.id] = repr(exc)
                logger.exception(
                    "backup key sync failed for %s", deployment.name
                )
    return written


async def cloud_backups_loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                await sync_keys(db)
        except Exception:  # the loop must survive one bad tick
            logger.exception("cloud backups tick failed")
        await asyncio.sleep(TICK_SECONDS)
