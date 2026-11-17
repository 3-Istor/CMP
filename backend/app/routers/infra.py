"""
Infrastructure Monitoring Router

Provides real-time health monitoring endpoints for:
- Global infrastructure (hypervisors, public clusters)
- Application-specific resources (per deployment)
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.routers.deployments import _get_app_or_404
from app.services import monitoring_service
from app.services.app_health import get_kubernetes_app_health
from app.services.authz import assert_project_role, is_cnp_admin
from app.services.clusters.state import cluster_states
from app.services.keycloak_service import get_current_user
from app.services.monitoring_service import (
    AppHealthResponse,
    ClusterStatus,
    GlobalHealthResponse,
)

logger = logging.getLogger(__name__)

# Concurrent app health reads of one batch call.
KUBE_CONCURRENCY = 4

router = APIRouter(
    prefix="/infra",
    tags=["Infrastructure"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/health", response_model=GlobalHealthResponse)
async def get_global_infrastructure_health(
    token_payload: dict = Depends(get_current_user),
):
    """
    Get health status of the global infrastructure: OpenStack hypervisors
    and the public clusters (EKS/GKE) state as seen by Argo CD.
    """
    try:
        health = await monitoring_service.get_global_health()
    except Exception as exc:
        logger.warning("Infrastructure health failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Failed to retrieve infrastructure health.",
        ) from exc
    health.clusters = [
        ClusterStatus(cloud=cloud.value, state=state)
        for cloud, state in (await cluster_states()).items()
    ]
    # Every signed-in user sees the home page; node addresses are for admins.
    if not is_cnp_admin(token_payload):
        health.openstack_hypervisors = [
            hv.model_copy(update={"ip": None})
            for hv in health.openstack_hypervisors
        ]
    return health


async def _can_read(db, token, project: str) -> bool:
    """GUEST on the project; a 403 hides it, any other error propagates."""
    try:
        await run_in_threadpool(
            assert_project_role, db, token, project, ProjectRole.GUEST
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


@router.get("/deployments/health", response_model=dict[int, AppHealthResponse])
async def get_deployments_health(
    ids: list[int] = Query(default=[]),
    token_payload: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Health of several Kubernetes apps in one call, keyed by deployment id.

    Ids that are unknown, deleted, not Kubernetes, not visible to the caller
    (GUEST on the project, as the single endpoint) or whose read fails are
    omitted.
    """
    rows = (
        db.query(Deployment)
        .filter(
            Deployment.id.in_(set(ids)),
            Deployment.provider_type == ProviderType.KUBERNETES,
            Deployment.status != DeploymentStatus.DELETED,
        )
        .all()
    )
    if not is_cnp_admin(token_payload):
        # Sequential: the Session is not thread-safe.
        visible = set()
        # A project-less app is refused to non-admins, as the single endpoint does.
        for project in {row.project_id for row in rows if row.project_id}:
            if await _can_read(db, token_payload, project):
                visible.add(project)
        rows = [row for row in rows if row.project_id in visible]

    limit = asyncio.Semaphore(KUBE_CONCURRENCY)

    async def read(row: Deployment):
        async with limit:
            try:
                return row.id, await get_kubernetes_app_health(row)
            except Exception as exc:  # one bad app must not sink the batch
                logger.warning(
                    "Health of deployment %s failed: %s", row.id, exc
                )
                return row.id, None

    return {
        id_: health
        for id_, health in await asyncio.gather(*(read(r) for r in rows))
        if health is not None
    }


@router.get(
    "/deployments/{deployment_id}/health", response_model=AppHealthResponse
)
async def get_deployment_health(
    deployment_id: int,
    token_payload: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get real-time health status for a specific deployment.

    Checks:
    - AWS Frontend: Auto Scaling Group instances + ALB target health
    - OpenStack Backend: Database VMs

    Returns aggregated health status:
    - "healthy": All components operational
    - "degraded": Some components unhealthy or provisioning
    - "down": No healthy components
    - "unknown": Unable to determine status

    Kubernetes apps also report asleep, not_connected and one entry per
    component in "components".

    Note: This uses direct SDK calls to get real-time status,
    bypassing Terraform state which may be stale for auto-scaling resources.
    """
    deployment = await run_in_threadpool(
        _get_app_or_404, db, token_payload, deployment_id, ProjectRole.GUEST
    )

    try:
        if deployment.provider_type == ProviderType.KUBERNETES:
            return await get_kubernetes_app_health(deployment)
        return await monitoring_service.get_app_health(deployment.name)
    except Exception as exc:
        logger.warning(
            "Health of deployment %s failed: %s", deployment_id, exc
        )
        raise HTTPException(
            status_code=503,
            detail="Failed to retrieve deployment health.",
        ) from exc
