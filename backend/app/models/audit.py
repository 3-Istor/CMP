from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditEvent(Base):
    """
    One change requested through the CMP. Commits it makes are signed by the
    GitHub App and applied by Argo CD, so this row is the only place the person
    behind a change is recorded.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    project: Mapped[str | None] = mapped_column(String(100), index=True)
    app: Mapped[str | None] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(255), default="")
    outcome: Mapped[str] = mapped_column(String(16))
    status_code: Mapped[int] = mapped_column(Integer)
    source_ip: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[str] = mapped_column(Text, default="{}")


class ActivityRecord(Base):
    """
    An audit event read from Loki or Argo CD by the activity collector, kept
    here so the Activity tab reads a table instead of scanning days of logs.
    """

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(80), unique=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    project: Mapped[str | None] = mapped_column(String(100), index=True)
    app: Mapped[str | None] = mapped_column(String(100))
    actor: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    notable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    target: Mapped[str] = mapped_column(String(512), default="")
    outcome: Mapped[str] = mapped_column(String(16))
    status_code: Mapped[int | None] = mapped_column(Integer)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[str] = mapped_column(Text, default="{}")


class ActivityCursor(Base):
    """How far each Loki source has been read."""

    __tablename__ = "activity_cursors"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
