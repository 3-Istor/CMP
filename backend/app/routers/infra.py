"""
Infrastructure Monitoring Router

Provides real-time health monitoring endpoints for:
- Global infrastructure (hypervisors, public clusters)
- Application-specific resources (per deployment)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import ProviderType
from app.routers.deployments import _get_app_or_404
from app.services import monitoring_service
from app.services.app_health import get_kubernetes_app_health
from app.services.authz import is_cnp_admin
from app.services.clusters.state import cluster_states
from app.services.keycloak_service import get_current_user
from app.services.monitoring_service import (
    AppHealthResponse,
    ClusterStatus,
    GlobalHealthResponse,
)

logger = logging.getLogger(__name__)

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
