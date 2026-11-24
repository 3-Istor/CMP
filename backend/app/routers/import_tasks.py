"""
GET /deployments/{id}/import-tasks: the "Finish the import" checklist of an
imported app, empty for a template app. Members, like the other app reads.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, DeploymentStatus
from app.models.security import SecurityScan
from app.routers.deployments import _cluster_exposure, _get_app_or_404
from app.services import app_repo
from app.services.imports import tasks
from app.services.keycloak_service import get_current_user
from app.services.observability import app_traces, stores
from app.services.security.model import Source

router = APIRouter(prefix="/deployments", tags=["Imports"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _code_scanned(db: Session, deployment: Deployment) -> bool:
    row = (
        db.query(SecurityScan)
        .filter_by(
            project=deployment.project_id,
            app=deployment.name,
            source=Source.CI.value,
        )
        .one_or_none()
    )
    return row is not None and row.status == "ok"


async def _has_traces(deployment: Deployment) -> bool | None:
    if not stores.capabilities()["traces"]:
        return None
    scope = stores.Scope(
        project=deployment.project_id,
        app=deployment.name,
        namespace=deployment.k8s_namespace
        or f"{deployment.project_id}-{deployment.name}",
        cloud="",
    )
    try:
        account = await run_in_threadpool(stores.trace_account, scope.project)
        found = await app_traces.list_traces(
            scope, account, "24h", 1, False, None, datetime.now(timezone.utc)
        )
    except (stores.NotAvailableError, stores.StoreError):
        return False
    return bool(found)


@router.get("/{deployment_id}/import-tasks")
async def get_import_tasks(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> dict:
    deployment = await run_in_threadpool(
        _get_app_or_404, db, token, deployment_id, ProjectRole.MEMBER
    )
    found = (
        app_repo.import_source_of(db, deployment.id)
        if app_repo.is_imported(deployment.template_id)
        else None
    )
    if found is None:
        return tasks.NOT_IMPORTED
    source, service = found
    running = deployment.status == DeploymentStatus.RUNNING
    state = tasks.AppState(
        running=running,
        route_live=running
        and await run_in_threadpool(_cluster_exposure, db, deployment)
        is not None,
        code_scanned=_code_scanned(db, deployment),
        has_traces=await _has_traces(deployment),
    )
    return tasks.view(source, service, tasks.compute(source, service, state))
