"""
Activity tab: who changed what in a project.
"""

import csv
import io
import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.models.audit import AuditEvent
from app.routers.finops import is_cnp_admin
from app.services.audit import NOTABLE
from app.services.keycloak_service import (
    get_current_user,
    require_project_role,
)

router = APIRouter(prefix="/activity", tags=["Activity"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


class ActivityEvent(BaseModel):
    id: int
    time: datetime
    source: str
    actor: str
    action: str
    notable: bool
    project: str | None
    app: str | None
    target: str
    outcome: str
    status_code: int
    source_ip: str | None
    details: dict[str, Any]


async def _role(token: dict, project: str | None) -> str:
    """ "platform", "admin" or "member"; 403 for anyone else."""
    if is_cnp_admin(token):
        return "platform"
    if not project:
        raise HTTPException(
            status_code=403,
            detail="Only platform admins can read activity across projects.",
        )
    return await run_in_threadpool(require_project_role, token, project)


def _query(
    db: Session,
    project: str | None,
    actor: str | None,
    action: str | None,
    app: str | None,
    since: datetime | None,
    until: datetime | None,
    notable_only: bool,
    before_id: int | None,
    limit: int,
) -> list[AuditEvent]:
    stmt = select(AuditEvent)
    if project:
        stmt = stmt.where(AuditEvent.project == project)
    if actor:
        stmt = stmt.where(AuditEvent.actor == actor)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if app:
        stmt = stmt.where(AuditEvent.app == app)
    if since:
        stmt = stmt.where(AuditEvent.created_at >= since)
    if until:
        stmt = stmt.where(AuditEvent.created_at < until)
    if notable_only:
        stmt = stmt.where(AuditEvent.action.in_(NOTABLE))
    if before_id:
        stmt = stmt.where(AuditEvent.id < before_id)
    stmt = stmt.order_by(AuditEvent.id.desc()).limit(limit)
    return list(db.scalars(stmt))


def _read(row: AuditEvent, role: str) -> ActivityEvent:
    return ActivityEvent(
        id=row.id,
        time=row.created_at,
        source="cmp",
        actor=row.actor,
        action=row.action,
        notable=row.action in NOTABLE,
        project=row.project,
        app=row.app,
        target=row.target,
        outcome=row.outcome,
        status_code=row.status_code,
        # Where someone connects from is for the people who manage access.
        source_ip=row.source_ip if role != "member" else None,
        details=json.loads(row.details or "{}"),
    )


@router.get("", response_model=list[ActivityEvent])
async def list_activity(
    token: CurrentUser,
    project: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    app: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    notable_only: bool = False,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: Session = Depends(get_db),
) -> list[ActivityEvent]:
    """Changes made through the CMP, newest first; page with ``before_id``."""
    role = await _role(token, project)
    rows = _query(
        db,
        project,
        actor,
        action,
        app,
        since,
        until,
        notable_only,
        before_id,
        limit,
    )
    return [_read(r, role) for r in rows]


_EXPORT_COLUMNS = [
    "time",
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
    actor: str | None = None,
    action: str | None = None,
    app: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    notable_only: bool = False,
    format: Annotated[str, Query(pattern="^(csv|json)$")] = "csv",
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """The filtered journal as CSV or JSON. Project admins and up."""
    role = await _role(token, project)
    if role == "member":
        raise HTTPException(
            status_code=403, detail="Exporting is for project admins."
        )
    rows = _query(
        db,
        project,
        actor,
        action,
        app,
        since,
        until,
        notable_only,
        None,
        10000,
    )
    events = [_read(r, role).model_dump(mode="json") for r in rows]
    name = f"activity-{project or 'all'}"
    if format == "json":
        return StreamingResponse(
            iter([json.dumps(events, indent=2)]),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.json"'
            },
        )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_EXPORT_COLUMNS)
    for event in events:
        writer.writerow([_csv_cell(event[c]) for c in _EXPORT_COLUMNS])
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )
