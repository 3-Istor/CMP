"""
Just-in-time access (CNP-124): platform admins request a bounded elevation,
another platform admin approves it, the CMP holds it in a Keycloak group.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.access import AccessRequest
from app.services.access import catalog, grants
from app.services.audit import note
from app.services.authz import is_cnp_admin
from app.services.keycloak_service import get_current_user
from app.services.step_up import assert_step_up

router = APIRouter(prefix="/access", tags=["access"])

HISTORY_SIZE = 50


def require_platform_admin(
    token: Annotated[dict, Depends(get_current_user)],
) -> dict:
    if not is_cnp_admin(token):
        raise HTTPException(
            status_code=403,
            detail="Just-in-time access is for platform admins (infra).",
        )
    return token


def require_human_admin(
    token: Annotated[dict, Depends(require_platform_admin)],
) -> dict:
    # An assistant token carries its user's groups, so is_cnp_admin alone
    # would let it request, approve or end an elevation on their behalf.
    if token.get("cnp_agent"):
        raise HTTPException(
            status_code=403,
            detail="An assistant cannot request, approve or end elevated "
            "access; a person does it in the CMP.",
        )
    return token


def require_step_up(
    token: Annotated[dict, Depends(require_human_admin)],
) -> dict:
    assert_step_up(token)
    return token


PlatformAdmin = Annotated[dict, Depends(require_platform_admin)]
HumanAdmin = Annotated[dict, Depends(require_human_admin)]
SteppedUp = Annotated[dict, Depends(require_step_up)]


def _username(token: dict) -> str:
    return token.get("preferred_username") or token.get("sub", "")


class ElevationRead(BaseModel):
    key: str
    group: str
    system: str


class CatalogRead(BaseModel):
    elevations: list[ElevationRead]
    default_minutes: int
    min_minutes: int
    max_minutes: int


class AccessRequestRead(BaseModel):
    id: int
    elevation: str
    requester: str
    reason: str
    duration_minutes: int
    status: str
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    activated_at: datetime | None
    expires_at: datetime | None
    ended_at: datetime | None
    ended_by: str | None

    model_config = {"from_attributes": True}

    @field_validator(
        "created_at",
        "decided_at",
        "activated_at",
        "expires_at",
        "ended_at",
        mode="after",
    )
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        # Stored naive UTC; sent with its zone so browsers do not read it
        # as local time.
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)


class AccessOverview(BaseModel):
    me: str
    catalog: CatalogRead
    pending: list[AccessRequestRead]
    active: list[AccessRequestRead]
    history: list[AccessRequestRead]


class AccessRequestCreate(BaseModel):
    elevation: str
    reason: str = Field(max_length=2000)
    duration_minutes: int


class DenyBody(BaseModel):
    reason: str = Field(default="", max_length=2000)


def _catalog() -> CatalogRead:
    return CatalogRead(
        elevations=[
            ElevationRead(key=e.key, group=e.group, system=e.system)
            for e in catalog.ELEVATIONS.values()
        ],
        default_minutes=catalog.default_minutes(),
        min_minutes=catalog.MIN_MINUTES,
        max_minutes=catalog.max_minutes(),
    )


def _read(row: AccessRequest) -> AccessRequestRead:
    return AccessRequestRead.model_validate(row)


def _raise(exc: grants.AccessError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _journal(request: Request, row: AccessRequest, **extra) -> None:
    note(
        request,
        target=f"{row.requester}:{row.elevation}",
        request_id=row.id,
        elevation=row.elevation,
        requester=row.requester,
        **extra,
    )


@router.get("", response_model=AccessOverview)
def overview(token: PlatformAdmin, db: Session = Depends(get_db)):
    """Everything open, and the latest closed: every admin sees all."""
    rows = (
        db.query(AccessRequest)
        .filter(AccessRequest.status.in_(grants.OPEN))
        .order_by(AccessRequest.created_at.desc())
        .all()
    )
    history = (
        db.query(AccessRequest)
        .filter(AccessRequest.status.notin_(grants.OPEN))
        .order_by(AccessRequest.created_at.desc())
        .limit(HISTORY_SIZE)
        .all()
    )
    return AccessOverview(
        me=_username(token),
        catalog=_catalog(),
        pending=[_read(r) for r in rows if r.status == grants.PENDING],
        active=[_read(r) for r in rows if r.status == grants.ACTIVE],
        history=[_read(r) for r in history],
    )


@router.post("/requests", response_model=AccessRequestRead, status_code=201)
async def create_request(
    body: AccessRequestCreate,
    request: Request,
    token: SteppedUp,
    db: Session = Depends(get_db),
):
    try:
        row = await run_in_threadpool(
            grants.create,
            db,
            _username(token),
            body.elevation,
            body.reason,
            body.duration_minutes,
        )
    except grants.AccessError as exc:
        raise _raise(exc) from exc
    _journal(request, row, duration_minutes=row.duration_minutes)
    await grants.announce(
        [grants.announcement("requested", row, row.requester)]
    )
    return _read(row)


@router.post(
    "/requests/{request_id}/approve", response_model=AccessRequestRead
)
async def approve_request(
    request_id: int,
    request: Request,
    token: SteppedUp,
    db: Session = Depends(get_db),
):
    approver = _username(token)
    try:
        row = await run_in_threadpool(grants.approve, db, request_id, approver)
    except grants.AccessError as exc:
        raise _raise(exc) from exc
    _journal(request, row, expires_at=row.expires_at)
    await grants.announce([grants.announcement("approved", row, approver)])
    return _read(row)


@router.post("/requests/{request_id}/deny", response_model=AccessRequestRead)
async def deny_request(
    request_id: int,
    body: DenyBody,
    request: Request,
    token: PlatformAdmin,
    db: Session = Depends(get_db),
):
    approver = _username(token)
    try:
        row = await run_in_threadpool(
            grants.deny, db, request_id, approver, body.reason
        )
    except grants.AccessError as exc:
        raise _raise(exc) from exc
    _journal(request, row)
    await grants.announce([grants.announcement("denied", row, approver)])
    return _read(row)


@router.post("/requests/{request_id}/cancel", response_model=AccessRequestRead)
async def cancel_request(
    request_id: int,
    request: Request,
    token: PlatformAdmin,
    db: Session = Depends(get_db),
):
    actor = _username(token)
    try:
        row = await run_in_threadpool(grants.cancel, db, request_id, actor)
    except grants.AccessError as exc:
        raise _raise(exc) from exc
    _journal(request, row)
    await grants.announce([grants.announcement("cancelled", row, actor)])
    return _read(row)


@router.post("/requests/{request_id}/end", response_model=AccessRequestRead)
async def end_grant(
    request_id: int,
    request: Request,
    token: HumanAdmin,
    db: Session = Depends(get_db),
):
    actor = _username(token)
    try:
        row = await run_in_threadpool(grants.end, db, request_id, actor)
    except grants.AccessError as exc:
        raise _raise(exc) from exc
    _journal(request, row)
    await grants.announce([grants.announcement("revoked", row, actor)])
    return _read(row)
