import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, ProviderType
from app.routers.deployments import _get_app_or_404
from app.services import power
from app.services.clusters.state import project_cloud
from app.services.environments import gitops
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError

router = APIRouter(prefix="/deployments", tags=["Power"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _kubernetes_app(
    db: Session, token: dict, deployment_id: int
) -> Deployment:
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.MEMBER)
    if deployment.provider_type != ProviderType.KUBERNETES:
        raise HTTPException(
            status_code=400,
            detail="Sleep, wake and restart are only available for Kubernetes apps.",
        )
    return deployment


def _unavailable(exc: KubeUnavailableError) -> HTTPException:
    if power.is_forbidden(exc):
        return HTTPException(
            status_code=403,
            detail=(
                "The cluster refuses this action to the CMP service account: "
                f"{exc}"
            ),
        )
    return HTTPException(status_code=503, detail=f"Cluster unreachable: {exc}")


@router.get("/{deployment_id}/power")
async def get_power(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, bool]:
    deployment = _kubernetes_app(db, token, deployment_id)
    cloud = project_cloud(db, deployment.project_id)
    try:
        state = await asyncio.to_thread(power.read, deployment, cloud)
    except KubeUnavailableError as exc:
        raise _unavailable(exc) from exc
    return {"asleep": state.asleep, "scheduled": state.scheduled}


async def _move(
    deployment_id: int, token: dict, db: Session, target: str
) -> dict[str, int]:
    deployment = _kubernetes_app(db, token, deployment_id)
    cloud = project_cloud(db, deployment.project_id)
    try:
        moved = await asyncio.to_thread(
            power.set_state, deployment, target, cloud
        )
    except power.NoScheduleError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "This app has no offhours schedule, so it cannot be put to "
                "sleep or woken manually."
            ),
        ) from exc
    except KubeUnavailableError as exc:
        raise _unavailable(exc) from exc
    return {"schedules": moved}


@router.post("/{deployment_id}/sleep")
async def sleep_deployment(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, int]:
    return await _move(deployment_id, token, db, power.MANUAL_SLEEP)


@router.post("/{deployment_id}/wake")
async def wake_deployment(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, int]:
    return await _move(deployment_id, token, db, power.MANUAL_WAKE)


@router.post("/{deployment_id}/restart")
async def restart_deployment(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> dict[str, list[str]]:
    deployment = _kubernetes_app(db, token, deployment_id)
    cloud = project_cloud(db, deployment.project_id)
    try:
        restarted = await asyncio.to_thread(
            power.restart, deployment, gitops.app_type(deployment), cloud
        )
    except power.AsleepError as exc:
        raise HTTPException(
            status_code=409,
            detail="The app is asleep: wake it before restarting it.",
        ) from exc
    except KubeUnavailableError as exc:
        raise _unavailable(exc) from exc
    return {"restarted": restarted}
