from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IssueReport(Base):
    """A difficulty an assistant met on the platform, sent with its user's
    consent (CNP-310). Identical reports share one row."""

    __tablename__ = "issue_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    tool: Mapped[str | None] = mapped_column(String(100), nullable=True)
    steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    project: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assistant: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reporters: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    occurrences: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="new"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )
    last_reported_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )


class IssueReportSubmission(Base):
    """One accepted call, for the per-user daily quota."""

    __tablename__ = "issue_report_submissions"
    __table_args__ = (
        Index("ix_issue_report_submissions_user_at", "username", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("issue_reports.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
