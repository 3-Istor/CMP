"""
Project export (EU Data Act Art. 25, "réversibilité"): the owner asks for an
archive of everything the platform holds for the project, and downloads it
during the 30-day retrieval period.

Owner only and with a fresh passkey: an export carries the project's
databases, objects and logs, so it is the largest disclosure the CMP can
make. Assistants are refused by the step-up check, and there is no MCP tool:
the download link is a bearer credential to all of that data.
"""

import json
import secrets as token_source
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.project_export import ProjectExport
from app.services.audit import note
from app.services.authz import RequireOwner
from app.services.project_export import runner
from app.services.project_export.sources import (
    S3ExportStore,
    export_store_configured,
)
from app.services.step_up import assert_step_up

router = APIRouter(prefix="/projects", tags=["Project export"])


class ExportRequest(BaseModel):
    # Values of the project's Vault secrets, on top of their names.
    include_secret_values: bool = False


class ExportRead(BaseModel):
    id: str
    project: str
    status: str
    requested_by: str
    include_secret_values: bool
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None
    size: int | None
    sha256: str | None
    problems: list[dict]


class ExportLink(BaseModel):
    url: str
    expires_in: int
    sha256: str


def _read(row: ProjectExport) -> ExportRead:
    return ExportRead(
        id=row.id,
        project=row.project,
        status=row.status,
        requested_by=row.requested_by,
        include_secret_values=row.include_secret_values,
        created_at=row.created_at,
        finished_at=row.finished_at,
        expires_at=row.expires_at,
        size=row.size,
        sha256=row.sha256,
        problems=json.loads(row.problems or "[]"),
    )


def _export(db: Session, project: str, export_id: str) -> ProjectExport:
    row = db.get(ProjectExport, export_id)
    if row is None or row.project != project:
        raise HTTPException(status_code=404, detail="Export not found.")
    return row


@router.post("/{project_name}/export", status_code=202)
async def request_export(
    payload: ExportRequest,
    ctx: RequireOwner,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ExportRead:
    """
    Start an export of the project. One at a time per project.

    Raises:
        401: step-up needed. 403: not the owner, or an assistant.
        409: an export of the project is already running.
        503: the export storage is not configured.
    """
    assert_step_up(ctx.token)
    if not export_store_configured():
        raise HTTPException(
            status_code=503, detail="Project export is not configured."
        )
    running = (
        db.query(ProjectExport)
        .filter(
            ProjectExport.project == ctx.project_name,
            ProjectExport.status.in_(runner.ACTIVE),
        )
        .first()
    )
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Export {running.id} of this project is still running.",
        )
    row = ProjectExport(
        id=token_source.token_hex(16),
        project=ctx.project_name,
        requested_by=ctx.username,
        include_secret_values=payload.include_secret_values,
        status="pending",
        created_at=runner.utcnow(),
        problems="[]",
    )
    db.add(row)
    db.commit()
    note(request, target=row.id)
    background_tasks.add_task(runner.run_export, row.id)
    return _read(row)


@router.get("/{project_name}/exports")
def list_exports(
    ctx: RequireOwner, db: Session = Depends(get_db)
) -> list[ExportRead]:
    rows = (
        db.query(ProjectExport)
        .filter(ProjectExport.project == ctx.project_name)
        .order_by(ProjectExport.created_at.desc())
        .all()
    )
    return [_read(row) for row in rows]


@router.get("/{project_name}/exports/{export_id}")
def get_export(
    export_id: str, ctx: RequireOwner, db: Session = Depends(get_db)
) -> ExportRead:
    return _read(_export(db, ctx.project_name, export_id))


@router.post("/{project_name}/exports/{export_id}/link")
def export_link(
    export_id: str,
    ctx: RequireOwner,
    request: Request,
    db: Session = Depends(get_db),
) -> ExportLink:
    """
    A download link for a finished export. It lives at most 7 days (the
    SigV4 limit) and never past the end of the retrieval period: ask again
    for a new one within that period.

    Raises:
        401: step-up needed. 404: unknown export.
        409: not finished, failed or expired.
    """
    assert_step_up(ctx.token)
    row = _export(db, ctx.project_name, export_id)
    now = runner.utcnow()
    if (
        row.status not in runner.DOWNLOADABLE
        or row.expires_at is None
        or row.expires_at <= now
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Export is {row.status}, not downloadable.",
        )
    seconds = min(
        settings.EXPORT_LINK_MAX_SECONDS,
        int((row.expires_at - now).total_seconds()),
    )
    note(request, target=row.id, expires_in=seconds)
    return ExportLink(
        url=S3ExportStore.from_settings().link(row.object_key, seconds),
        expires_in=seconds,
        sha256=row.sha256,
    )
