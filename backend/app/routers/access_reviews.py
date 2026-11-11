"""
Periodic access review (CNP-124): campaigns over every project and
platform group, decided by owners and infra, plus inactive accounts.
"""

import csv
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.access_review import (
    AccessReviewCampaign,
    AccessReviewItem,
    AccountActivity,
)
from app.services import grafana_service
from app.services.access_review import accounts, campaigns, directory
from app.services.access_review.notices import deliver
from app.services.audit import note
from app.services.authz import is_auditor, is_cnp_admin
from app.services.keycloak_service import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/access-reviews", tags=["access-reviews"])

HISTORY_SIZE = 20


def _username(token: dict) -> str:
    return token.get("preferred_username") or token.get("sub", "")


def require_reader(
    token: Annotated[dict, Depends(get_current_user)],
) -> dict:
    if not (is_cnp_admin(token) or is_auditor(token)):
        raise HTTPException(
            status_code=403,
            detail="Access reviews are for infra and security auditors.",
        )
    return token


def require_infra(
    token: Annotated[dict, Depends(get_current_user)],
) -> dict:
    if not is_cnp_admin(token):
        raise HTTPException(
            status_code=403, detail="Only infra manages access reviews."
        )
    return token


Reader = Annotated[dict, Depends(require_reader)]
Infra = Annotated[dict, Depends(require_infra)]
User = Annotated[dict, Depends(get_current_user)]


def _utc(value: datetime | None) -> datetime | None:
    # Stored naive UTC; sent with its zone so browsers do not read it as
    # local time.
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class _Utc(BaseModel):
    @field_validator("*", mode="after")
    @classmethod
    def _as_utc(cls, value):
        return _utc(value) if isinstance(value, datetime) else value


class ItemRead(_Utc):
    id: int
    campaign_id: int
    scope: str
    subject: str
    username: str
    role: str
    last_login_at: datetime | None
    reviewers: list[str]
    decision: str | None
    new_role: str | None
    note: str | None
    decided_by: str | None
    decided_at: datetime | None
    outcome: str | None
    outcome_detail: str | None
    task_done_by: str | None
    task_done_at: datetime | None


class CampaignRead(_Utc):
    id: int
    status: str
    trigger: str
    started_by: str
    started_at: datetime
    due_at: datetime
    escalated_at: datetime | None
    closed_at: datetime | None
    closed_by: str | None
    total: int = 0
    decided: int = 0
    open_tasks: int = 0


class CampaignDetail(BaseModel):
    campaign: CampaignRead
    items: list[ItemRead]


class AccountRead(_Utc):
    user_id: str
    username: str
    last_login_at: datetime | None
    tracked_since: datetime
    flagged_at: datetime | None
    disabled_at: datetime | None
    disable_due_at: datetime | None


class ReviewSettings(BaseModel):
    auto_start: bool
    interval_days: int
    deadline_days: int
    inactive_after_days: int
    inactive_grace_days: int
    inactive_disable_enabled: bool


class Overview(BaseModel):
    me: str
    can_manage: bool
    settings: ReviewSettings
    campaigns: list[CampaignRead]
    accounts: list[AccountRead]


class MineRead(_Utc):
    me: str
    campaign_id: int | None
    due_at: datetime | None
    items: list[ItemRead]


class StartBody(BaseModel):
    deadline_days: int | None = None


class DecisionBody(BaseModel):
    decision: Literal["keep", "remove", "change_role"]
    role: str | None = None
    note: str = Field(default="", max_length=2000)


def _item(row: AccessReviewItem) -> ItemRead:
    fields = {name: getattr(row, name) for name in campaigns.EXPORT_FIELDS}
    fields["reviewers"] = campaigns.reviewers_of(row)
    return ItemRead(id=row.id, **fields)


def _campaign(
    row: AccessReviewCampaign, items: list[AccessReviewItem]
) -> CampaignRead:
    return CampaignRead(
        id=row.id,
        status=row.status,
        trigger=row.trigger,
        started_by=row.started_by,
        started_at=row.started_at,
        due_at=row.due_at,
        escalated_at=row.escalated_at,
        closed_at=row.closed_at,
        closed_by=row.closed_by,
        total=len(items),
        decided=sum(1 for i in items if i.decision),
        open_tasks=sum(
            1
            for i in items
            if i.outcome == "terraform_task" and i.task_done_at is None
        ),
    )


def _account(row: AccountActivity) -> AccountRead:
    due = None
    if (
        settings.INACTIVE_DISABLE_ENABLED
        and row.flagged_at
        and row.disabled_at is None
    ):
        due = row.flagged_at + timedelta(days=settings.INACTIVE_GRACE_DAYS)
    return AccountRead(
        user_id=row.user_id,
        username=row.username,
        last_login_at=row.last_login_at,
        tracked_since=row.tracked_since,
        flagged_at=row.flagged_at,
        disabled_at=row.disabled_at,
        disable_due_at=due,
    )


def _raise(exc: campaigns.ReviewError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _get_campaign(db: Session, campaign_id: int) -> AccessReviewCampaign:
    row = db.get(AccessReviewCampaign, campaign_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return row


@router.get("", response_model=Overview)
def overview(token: Reader, db: Session = Depends(get_db)) -> Overview:
    rows = (
        db.query(AccessReviewCampaign)
        .order_by(AccessReviewCampaign.started_at.desc())
        .limit(HISTORY_SIZE)
        .all()
    )
    return Overview(
        me=_username(token),
        can_manage=is_cnp_admin(token),
        settings=ReviewSettings(
            auto_start=settings.ACCESS_REVIEW_AUTO_START,
            interval_days=settings.ACCESS_REVIEW_INTERVAL_DAYS,
            deadline_days=settings.ACCESS_REVIEW_DEADLINE_DAYS,
            inactive_after_days=settings.INACTIVE_AFTER_DAYS,
            inactive_grace_days=settings.INACTIVE_GRACE_DAYS,
            inactive_disable_enabled=settings.INACTIVE_DISABLE_ENABLED,
        ),
        campaigns=[_campaign(r, campaigns.items_of(db, r.id)) for r in rows],
        accounts=[_account(r) for r in accounts.flagged(db)],
    )


@router.post("/campaigns", response_model=CampaignRead, status_code=201)
async def start_campaign(
    request: Request,
    token: Infra,
    body: StartBody | None = None,
    db: Session = Depends(get_db),
) -> CampaignRead:
    try:
        row, notices = await run_in_threadpool(
            campaigns.start,
            db,
            _username(token),
            campaigns.utcnow(),
            "manual",
            body.deadline_days if body else None,
        )
    except campaigns.ReviewError as exc:
        raise _raise(exc) from exc
    items = campaigns.items_of(db, row.id)
    note(
        request,
        target=f"campaign:{row.id}",
        campaign_id=row.id,
        items=len(items),
        due_at=row.due_at,
    )
    await deliver(notices)
    return _campaign(row, items)


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
def campaign_detail(
    campaign_id: int, _: Reader, db: Session = Depends(get_db)
) -> CampaignDetail:
    row = _get_campaign(db, campaign_id)
    items = campaigns.items_of(db, row.id)
    return CampaignDetail(
        campaign=_campaign(row, items), items=[_item(i) for i in items]
    )


@router.post("/campaigns/{campaign_id}/close", response_model=CampaignRead)
def close_campaign(
    campaign_id: int,
    request: Request,
    token: Infra,
    db: Session = Depends(get_db),
) -> CampaignRead:
    try:
        row = campaigns.close(
            db, campaign_id, _username(token), campaigns.utcnow()
        )
    except campaigns.ReviewError as exc:
        raise _raise(exc) from exc
    items = campaigns.items_of(db, row.id)
    note(
        request,
        target=f"campaign:{row.id}",
        undecided=sum(1 for i in items if not i.decision),
    )
    return _campaign(row, items)


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(value)
    text = str(value)
    # Spreadsheets run cells starting with these as formulas.
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@router.get("/campaigns/{campaign_id}/export")
def export_campaign(
    campaign_id: int,
    _: Reader,
    fmt: Annotated[Literal["csv", "json"], Query(alias="format")] = "csv",
    db: Session = Depends(get_db),
) -> Response:
    row = _get_campaign(db, campaign_id)
    items = campaigns.items_of(db, row.id)
    rows = campaigns.export_rows(items)
    name = f"access-review-{row.id}"
    if fmt == "json":
        return JSONResponse(
            {
                "campaign": json.loads(
                    _campaign(row, items).model_dump_json()
                ),
                "items": rows,
            },
            headers={
                "Content-Disposition": f'attachment; filename="{name}.json"'
            },
        )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(campaigns.EXPORT_FIELDS)
    for item in rows:
        writer.writerow([_csv_cell(item[f]) for f in campaigns.EXPORT_FIELDS])
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


@router.get("/mine", response_model=MineRead)
def mine(
    token: User,
    project: str | None = None,
    db: Session = Depends(get_db),
) -> MineRead:
    """The project items the caller reviews in the open campaign."""
    campaign = campaigns.open_campaign(db)
    items = campaigns.pending_for(db, _username(token), project)
    return MineRead(
        me=_username(token),
        campaign_id=campaign.id if campaign else None,
        due_at=campaign.due_at if campaign else None,
        items=[_item(i) for i in items],
    )


async def _sync_grafana(item: AccessReviewItem) -> None:
    try:
        if item.decision == "remove":
            await grafana_service.remove_user_from_project_org(
                item.subject, item.username
            )
        elif item.decision == "change_role" and item.new_role:
            await grafana_service.add_user_to_project_org(
                item.subject, item.username, item.new_role
            )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Grafana sync failed for '%s' on '%s': %s",
            item.username,
            item.subject,
            exc,
        )


@router.post("/items/{item_id}/decision", response_model=ItemRead)
async def decide(
    item_id: int,
    body: DecisionBody,
    request: Request,
    token: User,
    db: Session = Depends(get_db),
) -> ItemRead:
    try:
        item, notices = await run_in_threadpool(
            campaigns.decide,
            db,
            item_id,
            _username(token),
            is_cnp_admin(token),
            body.decision,
            body.role,
            body.note,
        )
    except campaigns.ReviewError as exc:
        found = db.get(AccessReviewItem, item_id)
        if found is not None:
            note(
                request,
                target=found.username,
                project=found.subject if found.scope == "project" else None,
                scope=found.scope,
                subject=found.subject,
                decision=body.decision,
            )
        raise _raise(exc) from exc
    note(
        request,
        target=item.username,
        project=item.subject if item.scope == "project" else None,
        campaign_id=item.campaign_id,
        scope=item.scope,
        subject=item.subject,
        decision=item.decision,
        new_role=item.new_role,
        previous_role=item.role,
        outcome=item.outcome,
    )
    if item.outcome == "applied":
        await _sync_grafana(item)
    await deliver(notices)
    return _item(item)


@router.post("/items/{item_id}/task-done", response_model=ItemRead)
def task_done(
    item_id: int,
    request: Request,
    token: Infra,
    db: Session = Depends(get_db),
) -> ItemRead:
    try:
        item = campaigns.task_done(
            db, item_id, _username(token), campaigns.utcnow()
        )
    except campaigns.ReviewError as exc:
        raise _raise(exc) from exc
    note(request, target=f"{item.username}:{item.subject}")
    return _item(item)


@router.post("/accounts/{user_id}/enable", response_model=AccountRead)
async def enable_account(
    user_id: str,
    request: Request,
    _: Infra,
    db: Session = Depends(get_db),
) -> AccountRead:
    try:
        row = await run_in_threadpool(
            accounts.enable, db, user_id, campaigns.utcnow()
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail="No flagged account with this id."
        ) from exc
    except directory.DirectoryError as exc:
        raise HTTPException(
            status_code=502,
            detail="Keycloak refused the change, the account is unchanged.",
        ) from exc
    note(request, target=row.username)
    return _account(row)
