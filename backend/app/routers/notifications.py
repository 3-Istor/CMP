from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_serializer
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.notification import Notification
from app.services.keycloak_service import get_current_user
from app.services.notifications import utcnow

router = APIRouter(prefix="/notifications", tags=["Notifications"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


class NotificationRead(BaseModel):
    id: int
    project: str | None
    app: str | None
    kind: str
    title_key: str
    params: dict
    link: str | None
    created_at: datetime
    read_at: datetime | None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "read_at")
    def _as_utc(self, value: datetime | None) -> str | None:
        return f"{value.isoformat()}Z" if value else None


def _user_key(token: dict) -> str:
    key = token.get("preferred_username") or token.get("sub", "")
    if not key:
        raise HTTPException(status_code=400, detail="No user identity.")
    return key


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    token: CurrentUser,
    unread: bool = False,
    limit: int = Query(30, ge=1, le=100),
    before: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
) -> list[Notification]:
    """Own notifications, newest first. ``before`` is an id cursor."""
    query = select(Notification).where(
        Notification.user_key == _user_key(token)
    )
    if unread:
        query = query.where(Notification.read_at.is_(None))
    if before is not None:
        query = query.where(Notification.id < before)
    rows = db.scalars(query.order_by(Notification.id.desc()).limit(limit))
    return list(rows)


@router.get("/unread-count")
def unread_count(token: CurrentUser, db: Session = Depends(get_db)) -> dict:
    count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_key == _user_key(token),
            Notification.read_at.is_(None),
        )
    )
    return {"count": count or 0}


@router.post("/read-all")
def mark_all_read(token: CurrentUser, db: Session = Depends(get_db)) -> dict:
    result = db.execute(
        update(Notification)
        .where(
            Notification.user_key == _user_key(token),
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    db.commit()
    return {"updated": result.rowcount or 0}


@router.post("/{notification_id}/read", response_model=NotificationRead)
def mark_read(
    notification_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> Notification:
    row = db.scalars(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_key == _user_key(token),
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such notification.")
    if row.read_at is None:
        row.read_at = utcnow()
        db.commit()
        db.refresh(row)
    return row
