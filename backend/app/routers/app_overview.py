"""
Overview of an app: metrics, recent logs and Kubernetes events.

Members and up, like the other observability reads (activity, alerts, the
build log stream): metrics, logs and events can carry infrastructure detail
that guests do not get.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, ProviderType
from app.routers.deployments import _get_app_or_404
from app.services import activity_sources as sources
from app.services import app_overview as overview
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError

router = APIRouter(prefix="/deployments", tags=["App overview"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


class MetricPoint(BaseModel):
    time: datetime
    value: float


class MetricSeries(BaseModel):
    points: list[MetricPoint]
    current: float | None


class AppMetrics(BaseModel):
    window: str
    step_seconds: int
    # None when the app has no such series: no ingress traffic measured.
    requests_per_second: MetricSeries | None
    errors_5xx_percent: MetricSeries | None
    cpu_millicores: MetricSeries | None
    cpu_request_millicores: float | None


class AppLogLine(BaseModel):
    time: datetime
    component: str | None
    pod: str
    level: str | None
    line: str


class AppEvent(BaseModel):
    time: datetime
    type: str
    reason: str
    message: str
    count: int
    kind: str
    object: str


def _kubernetes_app(
    db: Session, token: dict, deployment_id: int
) -> tuple[Deployment, overview.Scope]:
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.MEMBER)
    if deployment.provider_type != ProviderType.KUBERNETES:
        raise HTTPException(
            status_code=400, detail="Only Kubernetes apps have this data."
        )
    try:
        return deployment, overview.scope_of(deployment)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _series(
    points: list[tuple[datetime, float]] | None
) -> MetricSeries | None:
    if not points:
        return None
    return MetricSeries(
        points=[MetricPoint(time=t, value=v) for t, v in points],
        current=points[-1][1],
    )


@router.get("/{deployment_id}/metrics", response_model=AppMetrics)
async def get_app_metrics(
    deployment_id: int,
    token: CurrentUser,
    window: Annotated[str, Query(pattern="^(1h|6h|24h)$")] = "24h",
    db: Session = Depends(get_db),
) -> AppMetrics:
    """Request rate, 5xx share and CPU of the prod environment."""
    _, scope = _kubernetes_app(db, token, deployment_id)
    try:
        data = await overview.app_metrics(
            scope, window, datetime.now(timezone.utc)
        )
    except overview.MetricsUnavailableError as exc:
        raise HTTPException(
            status_code=502, detail="Metrics are unavailable."
        ) from exc
    return AppMetrics(
        window=data["window"],
        step_seconds=data["step_seconds"],
        requests_per_second=_series(data["requests_per_second"]),
        errors_5xx_percent=_series(data["errors_5xx_percent"]),
        cpu_millicores=_series(data["cpu_millicores"]),
        cpu_request_millicores=data["cpu_request_millicores"],
    )


@router.get("/{deployment_id}/logs", response_model=list[AppLogLine])
async def get_app_logs(
    deployment_id: int,
    token: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 20,
    since: Annotated[str, Query(pattern="^(15m|1h|6h|24h)$")] = "1h",
    level: Annotated[
        str | None, Query(pattern="^(error|warn|info|debug)$")
    ] = None,
    component: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    db: Session = Depends(get_db),
) -> list[AppLogLine]:
    """Newest first. Read from the project's own Loki tenant only."""
    _, scope = _kubernetes_app(db, token, deployment_id)
    if component and component not in scope.components:
        raise HTTPException(status_code=400, detail="Unknown component.")
    if q and "`" in q:
        raise HTTPException(
            status_code=400, detail="Backticks are not allowed."
        )
    try:
        lines = await overview.app_logs(
            scope,
            component,
            level,
            q or None,
            since,
            limit,
            datetime.now(timezone.utc),
        )
    except sources.LokiUnavailableError as exc:
        raise HTTPException(
            status_code=502, detail="Logs are unavailable."
        ) from exc
    return [AppLogLine(**line) for line in lines]


@router.get("/{deployment_id}/events", response_model=list[AppEvent])
async def get_app_events(
    deployment_id: int,
    token: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    db: Session = Depends(get_db),
) -> list[AppEvent]:
    """Newest Kubernetes events of the app's pods, ReplicaSets and rollouts."""
    _, scope = _kubernetes_app(db, token, deployment_id)
    try:
        events = await run_in_threadpool(overview.app_events, scope, limit)
    except KubeUnavailableError as exc:
        raise HTTPException(
            status_code=502, detail="Events are unavailable."
        ) from exc
    return [AppEvent(**event) for event in events]
