from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
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
        DateTime(timezone=True),
        # Not server_default: CURRENT_TIMESTAMP drops the fraction, which breaks
        # the page cursor's text comparison on SQLite.
        default=lambda: datetime.now(timezone.utc),
        index=True,
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
    An audit event read from VictoriaLogs or Argo CD by the activity
    collector, kept here so the Activity tab reads a table instead of
    scanning days of logs.
    """

    __tablename__ = "activity_events"
    # No index on source alone: with five values, SQLite still picks it for
    # "source IN (...)" and sorts the whole table instead of walking time.
    # The window index covers the summary's GROUP BY without table reads.
    __table_args__ = (
        Index(
            "ix_activity_events_window",
            "time",
            "source",
            "is_read",
            "project",
            "actor",
            "action",
            "notable",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(80), unique=True)
    source: Mapped[str] = mapped_column(String(16))
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
    """How far each audit source has been read from VictoriaLogs."""

    __tablename__ = "activity_cursors"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
