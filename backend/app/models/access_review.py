from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccessReviewCampaign(Base):
    """
    One periodic access review. Items are a snapshot taken when it opens:
    someone added afterwards waits for the next campaign. Times are naive
    UTC, as SQLite stores them.
    """

    __tablename__ = "access_review_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # open, closed
    status: Mapped[str] = mapped_column(String(16), index=True)
    # manual, scheduled
    trigger: Mapped[str] = mapped_column(String(16))
    started_by: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_by: Mapped[str | None] = mapped_column(String(255))


class AccessReviewItem(Base):
    """One person's standing in one project or platform group."""

    __tablename__ = "access_review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("access_review_campaigns.id", ondelete="CASCADE"),
        index=True,
    )
    # project, platform
    scope: Mapped[str] = mapped_column(String(16))
    # The project name, or the platform group name.
    subject: Mapped[str] = mapped_column(String(100), index=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(32))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    # JSON list of the usernames allowed to decide.
    reviewers: Mapped[str] = mapped_column(Text, default="[]")
    # keep, remove, change_role
    decision: Mapped[str | None] = mapped_column(String(16))
    new_role: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    # applying, unchanged, applied, failed, terraform_task
    outcome: Mapped[str | None] = mapped_column(String(16))
    outcome_detail: Mapped[str | None] = mapped_column(Text)
    task_done_by: Mapped[str | None] = mapped_column(String(255))
    task_done_at: Mapped[datetime | None] = mapped_column(DateTime)


class AccountActivity(Base):
    """
    The last Keycloak login the CMP has seen for a realm user. Keycloak
    keeps login events for 30 days only: this row keeps the latest one for
    as long as the user exists, and ``tracked_since`` says from when the
    absence of a login means something.
    """

    __tablename__ = "account_activity"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    tracked_since: Mapped[datetime] = mapped_column(DateTime)
    flagged_at: Mapped[datetime | None] = mapped_column(DateTime)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime)
