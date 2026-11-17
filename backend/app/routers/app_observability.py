"""
The observability of one app for its pages and the MCP tools (CNP-185 V5):
golden signals, SLO and error budget, deploy markers and DORA, logs, traces,
alerts and costs.

Members and up, like the app's overview: the caller's rights are checked on
the app's project, then every store is read with a scope built from the
app's database row (namespace, project tenant). The browser chooses ranges,
limits and a search phrase from bounded lists; never a namespace, a label
or a tenant. A store that is not configured answers 503 not_available.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, ProviderType
from app.routers.deployments import _get_app_or_404
from app.routers.observability import SloRead, slo_read
from app.services import alerts_grafana
from app.services import app_overview as overview
from app.services import links
from app.services.authz import assert_project_role, is_cnp_admin, is_finops
from app.services.grafana_service import GRAFANA_BASE_URL
from app.services.keycloak_service import get_current_user
from app.services.observability import (
    app_alerts,
    app_costs,
    app_logs,
    app_traces,
    delivery,
    signals,
    stores,
)

router = APIRouter(prefix="/deployments", tags=["App observability"])

CurrentUser = Annotated[dict, Depends(get_current_user)]
SIGNAL_RANGE = "^(1h|6h|24h|7d)$"
LOG_RANGE = "^(15m|1h|6h|24h|7d)$"
TRACE_RANGE = "^(15m|1h|6h|24h)$"
TRACE_ID = "^[0-9a-fA-F]{16,32}$"


# ── Access ───────────────────────────────────────────────────────────────────


def _scope(deployment: Deployment) -> stores.Scope:
    if deployment.provider_type != ProviderType.KUBERNETES:
        raise HTTPException(
            status_code=400, detail="Only Kubernetes apps have this data."
        )
    try:
        found = overview.scope_of(deployment)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return stores.Scope(
        project=found.project,
        app=found.app,
        namespace=found.namespace,
        cloud=found.cloud.value,
    )


def _member_app(
    db: Session, token: dict, deployment_id: int
) -> tuple[Deployment, stores.Scope]:
    deployment = _get_app_or_404(db, token, deployment_id, ProjectRole.MEMBER)
    return deployment, _scope(deployment)


@contextmanager
def _reading() -> Iterator[None]:
    try:
        yield
    except stores.NotAvailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "not_available", "signal": exc.signal},
        ) from exc
    except stores.StoreError as exc:
        raise HTTPException(
            status_code=502, detail="The observability store did not answer."
        ) from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Models ───────────────────────────────────────────────────────────────────


class Point(BaseModel):
    time: datetime
    value: float


def _points(raw: list[tuple[datetime, float]]) -> list[Point]:
    return [Point(time=t, value=v) for t, v in raw]


class Capabilities(BaseModel):
    metrics: bool
    logs: bool
    traces: bool
    alerts: bool
    slo: bool
    silence_max_hours: int
    grafana_url: str


class RouteSignals(BaseModel):
    route: str
    requests_per_second: float
    error_percent: float
    p50_ms: float | None
    p95_ms: float | None


class GoldenSignals(BaseModel):
    range: str
    step_seconds: int
    no_traffic: bool
    rate: list[Point]
    error_percent: list[Point]
    p50_ms: list[Point]
    p95_ms: list[Point]
    routes: list[RouteSignals]


class Objective(BaseModel):
    kind: str
    target: float
    budget_remaining: float | None
    error_ratio: float | None
    burn_rate_1h: float | None
    burn_rate_6h: float | None
    trend: list[Point]


class SloStatus(BaseModel):
    settings: SloRead
    objectives: list[Objective]


class DeployMarker(BaseModel):
    time: datetime
    finished_at: datetime | None
    version: str | None
    strategy: str
    outcome: str
    component: str | None


class Dora(BaseModel):
    days: int
    deployments: int
    failed_changes: int
    deploys_per_week: float
    change_failure_rate: float | None


class ErrorPattern(BaseModel):
    pattern: str
    count: int
    trace_id: str | None
    last_seen: datetime | None


class LogLine(BaseModel):
    time: datetime
    message: str
    pod: str
    container: str
    level: str | None
    trace_id: str | None


class TraceSummary(BaseModel):
    trace_id: str
    root_service: str
    root_operation: str
    start: datetime
    duration_ms: float
    spans: int
    errors: int
    services: list[str]


class Span(BaseModel):
    span_id: str
    parent_id: str | None
    service: str
    operation: str
    start_us: int
    duration_us: int
    error: bool
    tags: dict[str, str]


class TraceDetail(TraceSummary):
    truncated: bool
    span_list: list[Span]


class AlertRead(BaseModel):
    fingerprint: str
    state: str
    silenced_by: list[str]
    starts_at: datetime
    labels: dict[str, str]
    summary: str | None
    description: str | None
    value: str | None
    runbook_url: str | None


class SilenceMatcher(BaseModel):
    name: str
    value: str


class AppSilence(BaseModel):
    id: str
    matchers: list[SilenceMatcher]
    starts_at: datetime
    ends_at: datetime
    created_by: str
    comment: str


class AppAlerts(BaseModel):
    alerts: list[AlertRead]
    silences: list[AppSilence]


class DailyCost(BaseModel):
    date: str
    total_eur: float


class WasteHint(BaseModel):
    title: str
    rec_type: str
    monthly_saving_eur: float
    justification: str


class Usage(BaseModel):
    cpu_request_cores: float | None
    cpu_p95_cores: float | None
    ram_request_gib: float | None
    ram_p99_gib: float | None
    history_days: float


class AppCosts(BaseModel):
    days: int
    cost_eur: float
    daily: list[DailyCost]
    requests: float | None
    metrics_available: bool
    cost_per_1k_requests_eur: float | None
    usage: Usage | None
    cpu_idle_ratio: float | None
    ram_idle_ratio: float | None
    recommendations: list[WasteHint]
    monthly_waste_eur: float


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/{deployment_id}/observability", response_model=Capabilities)
async def get_capabilities(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> Capabilities:
    """Which tabs have a store behind them."""
    _, scope = _member_app(db, token, deployment_id)
    try:
        org = await alerts_grafana.org_id(scope.project)
    except Exception:  # pylint: disable=broad-exception-caught
        # No org yet or Grafana unreachable: link the Grafana home.
        org = None
    return Capabilities(
        **stores.capabilities(),
        silence_max_hours=settings.OBS_SILENCE_MAX_HOURS,
        grafana_url=(
            GRAFANA_BASE_URL
            if org is None
            else links.grafana_app_url(org, scope.app, scope.namespace)
        ),
    )


@router.get(
    "/{deployment_id}/observability/signals", response_model=GoldenSignals
)
async def get_signals(
    deployment_id: int,
    token: CurrentUser,
    range_: Annotated[str, Query(alias="range", pattern=SIGNAL_RANGE)] = "24h",
    db: Session = Depends(get_db),
) -> GoldenSignals:
    """Requests, 5xx share, p50 and p95, overall and per route."""
    _, scope = _member_app(db, token, deployment_id)
    with _reading():
        data = await signals.golden_signals(scope, range_, _now())
    return GoldenSignals(
        range=data["range"],
        step_seconds=data["step_seconds"],
        no_traffic=data["no_traffic"],
        rate=_points(data["rate"]),
        error_percent=_points(data["error_percent"]),
        p50_ms=_points(data["p50_ms"]),
        p95_ms=_points(data["p95_ms"]),
        routes=[RouteSignals(**r) for r in data["routes"]],
    )


@router.get("/{deployment_id}/observability/slo", response_model=SloStatus)
async def get_slo_status(
    deployment_id: int,
    token: CurrentUser,
    range_: Annotated[str, Query(alias="range", pattern=SIGNAL_RANGE)] = "7d",
    db: Session = Depends(get_db),
) -> SloStatus:
    """The app's objectives, budget left and burn rates."""
    deployment = db.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    role = "admin"
    if not is_cnp_admin(token):
        found = assert_project_role(
            db, token, deployment.project_id or "", ProjectRole.MEMBER
        ).role
        if found not in (ProjectRole.ADMIN, ProjectRole.OWNER):
            role = "member"
    scope = _scope(deployment)
    targets = slo_read(db, scope.project, scope.app, role)
    with _reading():
        stores.require("slo")
        data = await signals.slo_status(
            scope, targets.period_days, _now(), range_
        )
    return SloStatus(
        settings=targets,
        objectives=[
            Objective(
                kind=o["kind"],
                target=(
                    targets.availability
                    if o["kind"] == "availability"
                    else targets.latency
                ),
                budget_remaining=o["budget_remaining"],
                error_ratio=o["error_ratio"],
                burn_rate_1h=o["burn_rate_1h"],
                burn_rate_6h=o["burn_rate_6h"],
                trend=_points(o["trend"]),
            )
            for o in data["objectives"]
        ],
    )


@router.get(
    "/{deployment_id}/observability/deploys",
    response_model=list[DeployMarker],
)
def get_deploys(
    deployment_id: int,
    token: CurrentUser,
    range_: Annotated[str, Query(alias="range", pattern=SIGNAL_RANGE)] = "24h",
    db: Session = Depends(get_db),
) -> list[DeployMarker]:
    """Prod rollouts in the range, for the markers on the graphs."""
    deployment, _ = _member_app(db, token, deployment_id)
    since = _now() - timedelta(seconds=signals.RANGES[range_])
    return [
        DeployMarker(**m) for m in delivery.markers(db, deployment.id, since)
    ]


@router.get("/{deployment_id}/observability/dora", response_model=Dora)
def get_dora(
    deployment_id: int,
    token: CurrentUser,
    days: Annotated[int, Query()] = 30,
    db: Session = Depends(get_db),
) -> Dora:
    """Deployment frequency and change failure rate of prod."""
    if days not in delivery.DORA_DAYS:
        raise HTTPException(status_code=422, detail="days: 7, 30 or 90.")
    deployment, _ = _member_app(db, token, deployment_id)
    return Dora(**delivery.dora(db, deployment.id, days, _now()))


@router.get(
    "/{deployment_id}/observability/logs/top-errors",
    response_model=list[ErrorPattern],
)
async def get_top_errors(
    deployment_id: int,
    token: CurrentUser,
    range_: Annotated[str, Query(alias="range", pattern=LOG_RANGE)] = "1h",
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    db: Session = Depends(get_db),
) -> list[ErrorPattern]:
    """The most frequent error lines, numbers folded into one pattern."""
    _, scope = _member_app(db, token, deployment_id)
    with _reading():
        rows = await app_logs.top_errors(scope, range_, limit)
    return [ErrorPattern(**r) for r in rows]


@router.get(
    "/{deployment_id}/observability/logs", response_model=list[LogLine]
)
async def search_logs(  # pylint: disable=too-many-arguments
    deployment_id: int,
    token: CurrentUser,
    range_: Annotated[str, Query(alias="range", pattern=LOG_RANGE)] = "1h",
    level: Annotated[
        str | None, Query(pattern="^(error|warn|info|debug)$")
    ] = None,
    q: Annotated[str | None, Query(max_length=app_logs.MAX_PHRASE)] = None,
    trace_id: Annotated[str | None, Query(pattern=TRACE_ID)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: Session = Depends(get_db),
) -> list[LogLine]:
    """Newest first, prod only."""
    _, scope = _member_app(db, token, deployment_id)
    try:
        if q:
            app_logs.phrase(q)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with _reading():
        rows = await app_logs.search(
            scope,
            range_,
            level,
            q or None,
            trace_id.lower() if trace_id else None,
            limit,
        )
    return [LogLine(**r) for r in rows]


@router.get(
    "/{deployment_id}/observability/traces",
    response_model=list[TraceSummary],
)
async def list_traces(  # pylint: disable=too-many-arguments
    deployment_id: int,
    token: CurrentUser,
    range_: Annotated[str, Query(alias="range", pattern=TRACE_RANGE)] = "1h",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    errors_only: bool = False,
    min_duration_ms: Annotated[int | None, Query(ge=1, le=600_000)] = None,
    db: Session = Depends(get_db),
) -> list[TraceSummary]:
    """Recent traces of the app, newest first."""
    _, scope = _member_app(db, token, deployment_id)
    with _reading():
        account = await run_in_threadpool(stores.trace_account, scope.project)
        rows = await app_traces.list_traces(
            scope, account, range_, limit, errors_only, min_duration_ms, _now()
        )
    return [TraceSummary(**r) for r in rows]


@router.get(
    "/{deployment_id}/observability/traces/{trace_id}",
    response_model=TraceDetail,
)
async def get_trace(
    deployment_id: int,
    trace_id: Annotated[str, Path(pattern=TRACE_ID)],
    token: CurrentUser,
    db: Session = Depends(get_db),
) -> TraceDetail:
    """One trace, read in the app's project tenant only."""
    _, scope = _member_app(db, token, deployment_id)
    with _reading():
        account = await run_in_threadpool(stores.trace_account, scope.project)
        found = await app_traces.trace_detail(account, trace_id.lower())
    if found is None:
        raise HTTPException(status_code=404, detail="Unknown trace.")
    return TraceDetail(**found)


@router.get("/{deployment_id}/observability/alerts", response_model=AppAlerts)
async def get_alerts(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> AppAlerts:
    """Alerts of the app in Alertmanager, and the silences covering it."""
    _, scope = _member_app(db, token, deployment_id)
    with _reading():
        data = await app_alerts.app_alerts(scope.project, scope.app)
    return AppAlerts(
        alerts=[AlertRead(**a) for a in data["alerts"]],
        silences=[_silence(s) for s in data["silences"]],
    )


def _silence(raw: dict[str, Any]) -> AppSilence:
    return AppSilence(
        id=raw["id"],
        matchers=[
            SilenceMatcher(name=m["name"], value=m["value"])
            for m in raw.get("matchers") or []
        ],
        starts_at=raw["startsAt"],
        ends_at=raw["endsAt"],
        created_by=raw.get("createdBy", ""),
        comment=raw.get("comment", ""),
    )


@router.get("/{deployment_id}/observability/costs", response_model=AppCosts)
async def get_costs(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> AppCosts:
    """Cost per 1,000 requests and requests against real usage."""
    if is_finops(token):
        deployment = db.get(Deployment, deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        scope = _scope(deployment)
    else:
        deployment, scope = _member_app(db, token, deployment_id)
    with _reading():
        data = await app_costs.app_costs(deployment, scope, _now())
    return AppCosts(**data)
