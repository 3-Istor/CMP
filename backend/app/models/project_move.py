from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

STEP_NAMES = (
    "prepare",
    "stop_source",
    "final_backup",
    "switch",
    "wait_target",
    "persist_restores",
    "cleanup_source",
    "finish",
)
# A failed move still blocks a new one until it is cancelled or retried.
ACTIVE = {"running", "failed", "cancelling"}


class ProjectMove(Base):
    """A project moving from one public cloud to the other, step by step."""

    __tablename__ = "project_moves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(32), default="button")
    # running | failed | cancelling | done | cancelled
    status: Mapped[str] = mapped_column(String(24), default="running")
    # [{"name", "status", "detail", "started_at", "finished_at"}]; status is
    # pending | running | done | failed | skipped (a skipped cleanup may carry "deferred")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    # Computed once in "prepare" (None until then), so a resume never bumps
    # the generation twice.
    restores: Mapped[list | None] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
