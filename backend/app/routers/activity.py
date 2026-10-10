"""
Activity tab: who changed what in a project, and the project's logs.

Sources: the CMP's own journal and Argo CD syncs for everyone in the project;
the Kubernetes API audit and Vault for admins (members see their own lines);
Keycloak logins for admins only.
"""

import asyncio
import csv
import io
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import get_db
from app.models.audit import ActivityCursor, ActivityRecord, AuditEvent
from app.routers.finops import is_cnp_admin
from app.services import activity_sources as sources
from app.services.audit import NOTABLE
from app.services.keycloak_service import (
    get_current_user,
    require_project_role,
)
from app.services.kube_client import KubeUnavailableError, kube_get

router = APIRouter(prefix="/activity", tags=["Activity"])

CurrentUser = Annotated[dict, Depends(get_current_user)]

ALL_SOURCES = ("cmp", "deployment", "kubernetes", "vault", "keycloak")
PARIS = ZoneInfo("Europe/Paris")
_LABEL_VALUE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


class ActivityEvent(BaseModel):
    id: str
    time: datetime
    source: str
    actor: str
    action: str
    notable: bool
    project: str | None
    app: str | None
    target: str
    outcome: str
    status_code: int | None
    source_ip: str | None
    details: dict[str, Any]


class ActivityFeed(BaseModel):
    events: list[ActivityEvent]
    # Sources that could not be read; the feed shows the others.
    unavailable: list[str]


class DayCount(BaseModel):
    day: date
    counts: dict[str, int]


class Kpi(BaseModel):
    value: int
    previous: int


class LoginDay(BaseModel):
    day: date
    success: int
    failure: int


class Logins(BaseModel):
    days: list[LoginDay]
    top_failed_users: list[tuple[str, int]]
    top_failed_ips: list[tuple[str, int]]


class ActivitySummary(BaseModel):
    actions: Kpi
    people: Kpi
    notable: Kpi
    login_failures: Kpi | None
    days: list[DayCount]
    top_actors: list[tuple[str, int]]
    # Actions by people per [weekday (0 = Monday), hour], Paris time.
    heatmap: list[list[int]]
    # Realm logins, for project admins and up.
    logins: Logins | None
    unavailable: list[str]


class DeniedFlow(BaseModel):
    source: str
    destination: str
    port: int | None
    protocol: str
    direction: str
    count: int
    last: datetime


class LogLine(BaseModel):
    time: datetime
    namespace: str
    pod: str
    container: str
    line: str


class LogTargets(BaseModel):
    namespaces: list[str]
    pods: list[str]
    containers: list[str]


# ── Access ───────────────────────────────────────────────────────────────────


async def _role(token: dict, project: str | None) -> str:
    """ "platform", "admin" or "member"; 403 for anyone else."""
    if project is not None and not sources.PROJECT_NAME.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name.")
    if is_cnp_admin(token):
        return "platform"
    if not project:
        raise HTTPException(
            status_code=403,
            detail="Only platform admins can read activity across projects.",
        )
    return await run_in_threadpool(require_project_role, token, project)


def _identities(token: dict) -> set[str]:
    """The names a person's own actions carry in each audit source."""
    names = {token.get("preferred_username"), token.get("email")}
    names |= {f"oidc-{n}" for n in list(names) if n}
    return {n for n in names if n}


def _visible(event: ActivityEvent, role: str, me: set[str]) -> bool:
    if role != "member":
        return True
    if event.source == "keycloak":
        return False
    if event.source in ("kubernetes", "vault"):
        return event.actor in me
    return True


# ── Collection ───────────────────────────────────────────────────────────────


def _cmp_rows(
    db: Session,
    project: str | None,
    since: datetime,
    until: datetime,
    limit: int,
) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(
        AuditEvent.created_at >= since, AuditEvent.created_at < until
    )
    if project:
        stmt = stmt.where(AuditEvent.project == project)
    stmt = stmt.order_by(AuditEvent.id.desc()).limit(limit)
    return list(db.scalars(stmt))


def _from_row(row: AuditEvent) -> ActivityEvent:
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return ActivityEvent(
        id=f"cmp-{row.id}",
        time=created,
        source="cmp",
        actor=row.actor,
        action=row.action,
        notable=row.action in NOTABLE,
        project=row.project,
        app=row.app,
        target=row.target,
        outcome=row.outcome,
        status_code=row.status_code,
        source_ip=row.source_ip or None,
        details=json.loads(row.details or "{}"),
    )


def _from_record(row: ActivityRecord) -> ActivityEvent:
    when = (
        row.time if row.time.tzinfo else row.time.replace(tzinfo=timezone.utc)
    )
    return ActivityEvent(
        id=row.external_id,
        time=when,
        source=row.source,
        actor=row.actor,
        action=row.action,
        notable=row.notable,
        project=row.project,
        app=row.app,
        target=row.target,
        outcome=row.outcome,
        status_code=row.status_code,
        source_ip=row.source_ip,
        details=json.loads(row.details or "{}"),
    )


def _collected_rows(
    db: Session,
    project: str | None,
    wanted: set[str],
    since: datetime,
    until: datetime,
    limit: int,
    include_reads: bool,
) -> list[ActivityRecord]:
    stmt = select(ActivityRecord).where(
        ActivityRecord.source.in_(wanted),
        ActivityRecord.time >= since,
        ActivityRecord.time < until,
    )
    if project:
        stmt = stmt.where(ActivityRecord.project == project)
    if not include_reads:
        stmt = stmt.where(ActivityRecord.is_read.is_(False))
    stmt = stmt.order_by(ActivityRecord.time.desc()).limit(limit)
    return list(db.scalars(stmt))


async def _collect(
    db: Session,
    project: str | None,
    wanted: set[str],
    since: datetime,
    until: datetime,
    limit: int,
    include_reads: bool,
) -> tuple[list[ActivityEvent], list[str]]:
    """The CMP journal and the collected sources, read from the database."""
    events: list[ActivityEvent] = []
    if "cmp" in wanted:
        rows = _cmp_rows(db, project, since, until, limit)
        events += [_from_row(r) for r in rows]
    collected = wanted - {"cmp"}
    if collected:
        records = _collected_rows(
            db, project, collected, since, until, limit, include_reads
        )
        events += [_from_record(r) for r in records]
    return events, []


def _window(
    since: datetime | None, until: datetime | None, days: int = 7
) -> tuple[datetime, datetime]:
    end = until or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = since or end - timedelta(days=days)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return start, end


def _filter(
    events: list[ActivityEvent],
    role: str,
    token: dict,
    actor: str | None,
    action: str | None,
    app: str | None,
    notable_only: bool,
) -> list[ActivityEvent]:
    me = _identities(token)
    kept = [
        e
        for e in events
        if _visible(e, role, me)
        and (not actor or e.actor == actor)
        and (not action or e.action.startswith(action))
        and (not app or e.app == app)
        and (not notable_only or e.notable)
    ]
    if role == "member":
        for e in kept:
            # Where someone connects from is for the people who manage access.
            e.source_ip = None
    kept.sort(key=lambda e: e.time, reverse=True)
    return kept


def _sources(param: str | None) -> set[str]:
    if not param:
        return set(ALL_SOURCES)
    wanted = {s.strip() for s in param.split(",") if s.strip()}
    unknown = wanted - set(ALL_SOURCES)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown source(s): {', '.join(sorted(unknown))}",
        )
    return wanted


# ── Feed ─────────────────────────────────────────────────────────────────────


@router.get("", response_model=ActivityFeed)
async def list_activity(
    token: CurrentUser,
    project: str | None = None,
    source: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    app: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    include_reads: bool = False,
    notable_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: Session = Depends(get_db),
) -> ActivityFeed:
    """Newest first. Page by passing the last event's time as ``until``."""
    role = await _role(token, project)
    start, end = _window(since, until)
    events, unavailable = await _collect(
        db, project, _sources(source), start, end, limit, include_reads
    )
    kept = _filter(events, role, token, actor, action, app, notable_only)
    return ActivityFeed(events=kept[:limit], unavailable=unavailable)


@router.get("/summary", response_model=ActivitySummary)
async def activity_summary(
    token: CurrentUser,
    project: str | None = None,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    db: Session = Depends(get_db),
) -> ActivitySummary:
    """Indicators against the period before, and counts per day and source."""
    role = await _role(token, project)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=2 * days)
    events, unavailable = await _collect(
        db, project, set(ALL_SOURCES), start, end, 5000, False
    )
    kept = _filter(events, role, token, None, None, None, False)
    middle = end - timedelta(days=days)
    current = [e for e in kept if e.time >= middle]
    previous = [e for e in kept if e.time < middle]

    def actions(evts):
        return [e for e in evts if e.source != "keycloak"]

    def people(evts):
        return {
            e.actor for e in actions(evts) if e.source not in ("deployment",)
        }

    def failures(evts):
        return [e for e in evts if e.action == "keycloak.login_error"]

    per_day: dict[date, Counter] = defaultdict(Counter)
    for e in actions(current):
        per_day[e.time.date()][e.source] += 1
    first = middle.date()
    days_list = [
        DayCount(
            day=first + timedelta(days=i),
            counts=dict(per_day.get(first + timedelta(days=i), {})),
        )
        for i in range(days + 1)
    ]
    top = Counter(
        e.actor for e in actions(current) if e.source != "deployment"
    )
    heatmap = [[0] * 24 for _ in range(7)]
    for e in actions(current):
        if e.source != "deployment":
            local = e.time.astimezone(PARIS)
            heatmap[local.weekday()][local.hour] += 1
    return ActivitySummary(
        actions=Kpi(
            value=len(actions(current)), previous=len(actions(previous))
        ),
        people=Kpi(value=len(people(current)), previous=len(people(previous))),
        notable=Kpi(
            value=sum(e.notable for e in current),
            previous=sum(e.notable for e in previous),
        ),
        login_failures=(
            None
            if role == "member"
            else Kpi(
                value=len(failures(current)), previous=len(failures(previous))
            )
        ),
        days=days_list,
        top_actors=top.most_common(5),
        heatmap=heatmap,
        logins=None if role == "member" else _logins(current, first, days),
        unavailable=unavailable,
    )


def _logins(events: list[ActivityEvent], first: date, days: int) -> Logins:
    per_day: dict[date, Counter] = defaultdict(Counter)
    failed_users: Counter = Counter()
    failed_ips: Counter = Counter()
    for e in events:
        if e.action == "keycloak.login":
            per_day[e.time.date()]["success"] += 1
        elif e.action == "keycloak.login_error":
            per_day[e.time.date()]["failure"] += 1
            failed_users[e.actor or "?"] += 1
            failed_ips[e.source_ip or "?"] += 1
    return Logins(
        days=[
            LoginDay(
                day=first + timedelta(days=i),
                success=per_day[first + timedelta(days=i)]["success"],
                failure=per_day[first + timedelta(days=i)]["failure"],
            )
            for i in range(days + 1)
        ],
        top_failed_users=failed_users.most_common(5),
        top_failed_ips=failed_ips.most_common(5),
    )


@router.get("/network", response_model=list[DeniedFlow])
async def denied_flows(
    token: CurrentUser,
    project: str,
    days: Annotated[int, Query(ge=1, le=30)] = 1,
    db: Session = Depends(get_db),
) -> list[DeniedFlow]:
    """Flows a network policy dropped on the project's side, by pair and port."""
    await _role(token, project)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.scalars(
        select(ActivityRecord).where(
            ActivityRecord.source == "network",
            ActivityRecord.project == project,
            ActivityRecord.time >= since,
        )
    )
    groups: dict[tuple, DeniedFlow] = {}
    for row in rows:
        d = json.loads(row.details or "{}")
        when = (
            row.time
            if row.time.tzinfo
            else row.time.replace(tzinfo=timezone.utc)
        )
        key = (
            row.actor,
            row.target,
            d.get("port"),
            d.get("protocol"),
            d.get("direction"),
        )
        flow = groups.get(key)
        if flow:
            flow.count += 1
            flow.last = max(flow.last, when)
        else:
            groups[key] = DeniedFlow(
                source=row.actor,
                destination=row.target,
                port=d.get("port"),
                protocol=d.get("protocol") or "",
                direction=d.get("direction") or "",
                count=1,
                last=when,
            )
    return sorted(groups.values(), key=lambda f: f.last, reverse=True)


_EXPORT_COLUMNS = [
    "time",
    "source",
    "actor",
    "action",
    "notable",
    "project",
    "app",
    "target",
    "outcome",
    "status_code",
    "source_ip",
    "details",
]


def _csv_cell(value: Any) -> str:
    text = json.dumps(value) if isinstance(value, dict) else str(value or "")
    # A cell starting with = + - @ runs as a formula in a spreadsheet.
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@router.get("/export")
async def export_activity(
    token: CurrentUser,
    project: str | None = None,
    source: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    app: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    include_reads: bool = False,
    notable_only: bool = False,
    format: Annotated[str, Query(pattern="^(csv|json)$")] = "csv",
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """The filtered feed as CSV or JSON. Project admins and up."""
    role = await _role(token, project)
    if role == "member":
        raise HTTPException(
            status_code=403, detail="Exporting is for project admins."
        )
    start, end = _window(since, until)
    events, _ = await _collect(
        db, project, _sources(source), start, end, 5000, include_reads
    )
    kept = _filter(events, role, token, actor, action, app, notable_only)
    rows = [e.model_dump(mode="json") for e in kept]
    name = f"activity-{project or 'all'}"
    if format == "json":
        return StreamingResponse(
            iter([json.dumps(rows, indent=2)]),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.json"'
            },
        )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([_csv_cell(row[c]) for c in _EXPORT_COLUMNS])
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


# ── Logs ─────────────────────────────────────────────────────────────────────


def _label(value: str | None, name: str) -> str | None:
    if value and not _LABEL_VALUE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {name}.")
    return value


@router.get("/logs", response_model=list[LogLine])
async def project_logs(
    token: CurrentUser,
    project: str,
    namespace: str | None = None,
    pod: str | None = None,
    container: str | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    level: Annotated[str | None, Query(pattern="^(error|warn)$")] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[LogLine]:
    """The project's pod logs, newest first; every member may read them."""
    await _role(token, project)
    if search and "`" in search:
        raise HTTPException(
            status_code=400, detail="Backticks are not allowed."
        )
    start, end = _window(since, until, days=1)
    try:
        lines = await sources.app_logs(
            project,
            _label(namespace, "namespace"),
            _label(pod, "pod"),
            _label(container, "container"),
            search,
            level,
            start,
            end,
            limit,
        )
    except sources.LokiUnavailableError as exc:
        raise HTTPException(
            status_code=502, detail="Logs are unavailable."
        ) from exc
    return [LogLine(**line.__dict__) for line in lines]


@router.get("/logs/targets", response_model=LogTargets)
async def log_targets(token: CurrentUser, project: str) -> LogTargets:
    """Namespaces, pods and containers with logs in the last 24 hours."""
    await _role(token, project)
    start, end = _window(None, None, days=1)
    try:
        namespaces, pods, containers = await asyncio.gather(
            *(
                sources.log_label_values(project, label, start, end)
                for label in ("namespace", "pod", "container")
            )
        )
    except sources.LokiUnavailableError as exc:
        raise HTTPException(
            status_code=502, detail="Logs are unavailable."
        ) from exc
    return LogTargets(namespaces=namespaces, pods=pods, containers=containers)


# ── Platform ─────────────────────────────────────────────────────────────────


class ActiveAlert(BaseModel):
    name: str
    severity: str
    summary: str
    since: datetime


class PlatformStatus(BaseModel):
    alerts: list[ActiveAlert]
    archive_last_success: datetime | None
    archive_last_schedule: datetime | None
    # How far each audit source has been collected.
    collected_until: dict[str, datetime]


def _archive_status() -> tuple[datetime | None, datetime | None]:
    cronjob = kube_get(
        "/apis/batch/v1/namespaces/observability/cronjobs/audit-archive"
    )
    status = (cronjob or {}).get("status", {})

    def parse(value: str | None) -> datetime | None:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if value
            else None
        )

    return parse(status.get("lastSuccessfulTime")), parse(
        status.get("lastScheduleTime")
    )


@router.get("/platform", response_model=PlatformStatus)
async def platform_status(
    token: CurrentUser, db: Session = Depends(get_db)
) -> PlatformStatus:
    """Active alerts, archive and collector health. CNP admins only."""
    if await _role(token, None) != "platform":
        raise HTTPException(status_code=403, detail="CNP admins only.")
    alerts: list[ActiveAlert] = []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{settings.ALERTMANAGER_URL}/api/v2/alerts",
                params={"active": "true", "silenced": "false"},
            )
        for a in response.json() if response.status_code == 200 else []:
            alerts.append(
                ActiveAlert(
                    name=a["labels"].get("alertname", "?"),
                    severity=a["labels"].get("severity", ""),
                    summary=a.get("annotations", {}).get("summary", ""),
                    since=a["startsAt"],
                )
            )
    except httpx.HTTPError:
        pass
    try:
        last_success, last_schedule = await run_in_threadpool(_archive_status)
    except KubeUnavailableError:
        last_success = last_schedule = None
    cursors = {
        c.source: (
            c.until if c.until.tzinfo else c.until.replace(tzinfo=timezone.utc)
        )
        for c in db.scalars(select(ActivityCursor))
    }
    return PlatformStatus(
        alerts=sorted(alerts, key=lambda a: a.since, reverse=True),
        archive_last_success=last_success,
        archive_last_schedule=last_schedule,
        collected_until=cursors,
    )
