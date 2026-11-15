"""
SLO targets of an app and silences of a project's alerts (CNP-185, V3).

Members see the SLO and mute their project's alerts; admins set the
targets. Every silence is bound to the caller's project server side.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import get_db
from app.models.agent import ProjectAgentSettings
from app.models.deployment import Deployment, DeploymentStatus
from app.models.observability import SloSetting
from app.services.activity_sources import PROJECT_NAME
from app.services.agents.policy import DEFAULT_LEVEL
from app.services.authz import is_cnp_admin, require_project_member
from app.services.keycloak_service import get_current_user
from app.services.observability import reconcile, silences, slo

router = APIRouter(prefix="/observability", tags=["Observability"])

CurrentUser = Annotated[dict, Depends(get_current_user)]
SILENCE_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _enabled() -> None:
    if not settings.OBS_V3_ENABLED:
        raise HTTPException(status_code=404, detail="Not enabled.")


async def _role(token: dict, project: str) -> str:
    if not PROJECT_NAME.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name.")
    if is_cnp_admin(token):
        return "admin"
    return await run_in_threadpool(require_project_member, token, project)


def _author(token: dict) -> str:
    return token.get("preferred_username") or token.get("sub", "")


def _deployment(db: Session, project: str, app: str) -> Deployment:
    row = db.scalar(
        select(Deployment).where(
            Deployment.project_id == project,
            Deployment.name == app,
            Deployment.status != DeploymentStatus.DELETED,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown app.")
    return row


class SloRead(BaseModel):
    project: str
    app: str
    period_days: int
    availability: float
    latency: float
    latency_threshold_ms: int
    can_edit: bool


class SloUpdate(BaseModel):
    project: str
    app: str
    availability: float = Field(ge=90.0, le=99.99)
    latency: float = Field(ge=50.0, le=99.9)


def _slo_read(db: Session, project: str, app: str, role: str) -> SloRead:
    setting = db.scalar(
        select(SloSetting).where(
            SloSetting.project == project, SloSetting.app == app
        )
    )
    level = db.scalar(
        select(ProjectAgentSettings.level).where(
            ProjectAgentSettings.project_name == project
        )
    )
    return SloRead(
        project=project,
        app=app,
        period_days=slo.period_days(level or DEFAULT_LEVEL),
        availability=(
            setting.availability if setting else slo.DEFAULT_AVAILABILITY
        ),
        latency=setting.latency if setting else slo.DEFAULT_LATENCY,
        latency_threshold_ms=slo.LATENCY_THRESHOLD_MS,
        can_edit=role == "admin",
    )


@router.get("/slo", response_model=SloRead)
async def get_slo(
    token: CurrentUser,
    project: str,
    app: str,
    db: Session = Depends(get_db),
) -> SloRead:
    _enabled()
    role = await _role(token, project)
    _deployment(db, project, app)
    return _slo_read(db, project, app, role)


@router.put("/slo", response_model=SloRead)
async def set_slo(
    payload: SloUpdate,
    token: CurrentUser,
    db: Session = Depends(get_db),
) -> SloRead:
    """Set an app's targets. Project admins."""
    _enabled()
    if await _role(token, payload.project) != "admin":
        raise HTTPException(status_code=403, detail="Project admins only.")
    _deployment(db, payload.project, payload.app)
    setting = db.scalar(
        select(SloSetting).where(
            SloSetting.project == payload.project,
            SloSetting.app == payload.app,
        )
    ) or SloSetting(project=payload.project, app=payload.app)
    setting.availability = payload.availability
    setting.latency = payload.latency
    setting.updated_by = _author(token)
    db.add(setting)
    db.commit()
    reconcile.request_sync()
    return _slo_read(db, payload.project, payload.app, "admin")


class Matcher(BaseModel):
    name: str
    value: str


class SilenceRead(BaseModel):
    id: str
    matchers: list[Matcher]
    starts_at: datetime
    ends_at: datetime
    created_by: str
    comment: str
    state: str


class SilenceCreate(BaseModel):
    project: str
    # Exact values on alertname, cnp_app, cnp_slo or severity.
    matchers: dict[str, str] = {}
    hours: float = Field(gt=0)
    comment: str = Field(default="", max_length=500)


def _silence_read(raw: dict) -> SilenceRead:
    return SilenceRead(
        id=raw["id"],
        matchers=[
            Matcher(name=m["name"], value=m["value"])
            for m in raw.get("matchers") or []
        ],
        starts_at=raw["startsAt"],
        ends_at=raw["endsAt"],
        created_by=raw.get("createdBy", ""),
        comment=raw.get("comment", ""),
        state=(raw.get("status") or {}).get("state", ""),
    )


@router.get("/silences", response_model=list[SilenceRead])
async def list_silences(token: CurrentUser, project: str) -> list[SilenceRead]:
    _enabled()
    await _role(token, project)
    try:
        found = await silences.list_for(project)
    except silences.AlertmanagerError as exc:
        raise HTTPException(
            status_code=502, detail="Alertmanager is unavailable."
        ) from exc
    return [_silence_read(s) for s in found]


@router.post("/silences", response_model=SilenceRead, status_code=201)
async def create_silence(
    payload: SilenceCreate, token: CurrentUser
) -> SilenceRead:
    """Mute some of the project's alerts. Project members."""
    _enabled()
    await _role(token, payload.project)
    try:
        body = silences.build(
            payload.project,
            payload.matchers,
            timedelta(hours=payload.hours),
            _author(token),
            payload.comment,
            datetime.now(timezone.utc),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        silence_id = await silences.create(body)
    except silences.AlertmanagerError as exc:
        raise HTTPException(
            status_code=502, detail="Alertmanager is unavailable."
        ) from exc
    return _silence_read(
        {**body, "id": silence_id, "status": {"state": "active"}}
    )


@router.delete("/silences/{silence_id}", status_code=204)
async def expire_silence(
    silence_id: Annotated[str, Path()],
    token: CurrentUser,
    project: str,
) -> None:
    _enabled()
    await _role(token, project)
    if not SILENCE_ID.match(silence_id):
        raise HTTPException(status_code=404, detail="Unknown silence.")
    try:
        await silences.expire(project, silence_id)
    except silences.SilenceNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Unknown silence."
        ) from exc
    except silences.AlertmanagerError as exc:
        raise HTTPException(
            status_code=502, detail="Alertmanager is unavailable."
        ) from exc
