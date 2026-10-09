from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SecurityFinding(Base):
    """
    One problem seen by one source. The same fingerprint seen by two sources
    is two rows, merged when read: each source then resolves only its own.
    """

    __tablename__ = "security_findings"
    __table_args__ = (UniqueConstraint("project", "fingerprint", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(100), index=True)
    app: Mapped[str | None] = mapped_column(String(100), index=True)
    fingerprint: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(16))
    audience: Mapped[str] = mapped_column(String(16))
    rule: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    fix: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(512))
    raw: Mapped[str] = mapped_column(Text, default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class SecurityScan(Base):
    """
    Last and next run of one source, per app. Project-wide rows use an empty
    app rather than NULL, which a unique constraint would not compare.
    """

    __tablename__ = "security_scans"
    __table_args__ = (UniqueConstraint("project", "app", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(100), index=True)
    app: Mapped[str] = mapped_column(String(100), default="")
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="ok")
    message: Mapped[str] = mapped_column(Text, default="")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    artifact_id: Mapped[str | None] = mapped_column(String(64))


class SecurityException(Base):
    """
    A finding a developer chose to ignore. The vocabulary is VEX's for
    "not affected", plus false positives, accepted risks and revoked secrets.
    """

    __tablename__ = "security_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(100), index=True)
    app: Mapped[str | None] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(32), index=True)
    rule: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    justification: Mapped[str | None] = mapped_column(String(64))
    statement: Mapped[str] = mapped_column(Text)
    expires_on: Mapped[date] = mapped_column(Date)
    author: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_by: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    commit_sha: Mapped[str | None] = mapped_column(String(64))


class SecuritySnapshot(Base):
    """Daily score of an app (empty app: the project), for the trend graphs."""

    __tablename__ = "security_snapshots"
    __table_args__ = (UniqueConstraint("day", "project", "app"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    project: Mapped[str] = mapped_column(String(100), index=True)
    app: Mapped[str] = mapped_column(String(100), default="")
    score: Mapped[int] = mapped_column(Integer)
    grade: Mapped[str] = mapped_column(String(1))
    core: Mapped[int] = mapped_column(Integer, default=0)
    important: Mapped[int] = mapped_column(Integer, default=0)
    recommended: Mapped[int] = mapped_column(Integer, default=0)
    new_major: Mapped[int] = mapped_column(Integer, default=0)
