from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccessRequest(Base):
    """
    One just-in-time elevation: asked by a platform admin, decided by
    another, then held in a Keycloak group until it expires or is ended.
    Times are naive UTC, as SQLite stores them.
    """

    __tablename__ = "access_requests"
    __table_args__ = (
        Index("ix_access_requests_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elevation: Mapped[str] = mapped_column(String(64), index=True)
    requester: Mapped[str] = mapped_column(String(255), index=True)
    reason: Mapped[str] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    # pending, active, denied, cancelled, lapsed, expired, revoked, failed
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_by: Mapped[str | None] = mapped_column(String(255))
