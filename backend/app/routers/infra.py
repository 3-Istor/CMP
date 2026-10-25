"""
Infrastructure Monitoring Router

Provides real-time health monitoring endpoints for:
- Global infrastructure (VPNs, hypervisors)
- Application-specific resources (per deployment)
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.cluster import Cluster, ClusterState
from app.models.deployment import Deployment
from app.services import monitoring_service
from app.services.authz import is_cnp_admin
from app.services.clusters.ops import wireguard_running
from app.services.clusters.recovery import reconcile
from app.services.clusters.state import PUBLIC_CLOUDS
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
    token: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """
    Get health status of the global infrastructure: OpenStack hypervisors
    and the public clusters (EKS/GKE) with their up/down state.
    """
    try:
        health = await monitoring_service.get_global_health()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to retrieve infrastructure health: {str(exc)}",
        ) from exc
    # Cluster info is best effort: hypervisors are returned whatever happens.
    health.can_operate_clusters = is_cnp_admin(token)
    try:
        await reconcile(db)
        rows = {r.cloud: r for r in db.query(Cluster).all()}
        for cloud in PUBLIC_CLOUDS:
            row = rows.get(cloud)
            status = (
                ClusterStatus.model_validate(row)
                if row else ClusterStatus(cloud=cloud.value, state="down")
            )
            if row and row.state == ClusterState.UP:
                status.wireguard_running = await wireguard_running(cloud)
            health.clusters.append(status)
    except Exception:  # noqa: BLE001
        logger.warning("Cluster status unavailable", exc_info=True)
    return health


@router.get(
    "/deployments/{deployment_id}/health", response_model=AppHealthResponse
)
async def get_deployment_health(
    deployment_id: int, db: Session = Depends(get_db)
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

    Note: This uses direct SDK calls to get real-time status,
    bypassing Terraform state which may be stale for auto-scaling resources.
    """
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    try:
        return await monitoring_service.get_app_health(deployment.name)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to retrieve deployment health: {str(exc)}",
        ) from exc
