"""Repository imports: a repository bound to a project, its runs and apps."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class RunKind(str, enum.Enum):
    ANALYZE = "analyze"
    BUILD = "build"


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
)


class ImportSource(Base):
    __tablename__ = "import_sources"
    __table_args__ = (UniqueConstraint("project_name", "repo_id"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    project_name: Mapped[str] = mapped_column(String(100), index=True)
    repo_full_name: Mapped[str] = mapped_column(String(200))
    repo_id: Mapped[int] = mapped_column(Integer)
    # The installation the repository was proven through; tokens are always
    # minted down-scoped to this one repository.
    installation_id: Mapped[int] = mapped_column(Integer)
    branch: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )
    # Who an automatic rebuild runs as: the creator, then the last admin who
    # confirmed a rebuild with step-up. Their rights are checked again on
    # every rebuild.
    rebuild_user: Mapped[str | None] = mapped_column(String(255))
    rebuild_user_sub: Mapped[str | None] = mapped_column(String(64))
    auto_rebuild: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true()
    )
    # Last branch head the CMP resolved itself and found nothing to build at.
    head_checked: Mapped[str | None] = mapped_column(String(40))
    # Set when an automatic rebuild could not run as rebuild_user: an admin
    # confirms it from the CMP.
    rebuild_pending: Mapped[dict | None] = mapped_column(JSON)

    runs: Mapped[list["ImportRun"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="ImportRun.created_at",
    )
    services: Mapped[list["ImportService"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("import_sources.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    sha: Mapped[str] = mapped_column(String(40))
    # Sent to the workflow as an input and echoed back on every worker call:
    # a run of an older dispatch cannot act on a newer attempt.
    attempt_id: Mapped[str] = mapped_column(String(36), default=_uuid)
    # GitHub's run id and run attempt, bound at the first claim: a re-run of
    # the workflow run (same run id, next attempt) cannot act on it.
    workflow_run_id: Mapped[str | None] = mapped_column(String(32))
    workflow_run_attempt: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(
        String(16), default=RunStatus.QUEUED.value, index=True
    )
    plan: Mapped[dict | None] = mapped_column(JSON)
    report: Mapped[dict | None] = mapped_column(JSON)
    requested_by: Mapped[str] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    source: Mapped[ImportSource] = relationship(back_populates="runs")


class ImportService(Base):
    """
    One app of an import, service or backing. ``spec`` holds what the
    registry entry and the values file are generated from.
    """

    __tablename__ = "import_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("import_sources.id", ondelete="CASCADE"), index=True
    )
    app_name: Mapped[str] = mapped_column(String(63))
    kind: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(String(512), default="")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    last_sha: Mapped[str | None] = mapped_column(String(40))
    last_digest: Mapped[str | None] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    message: Mapped[str] = mapped_column(Text, default="")
    deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="SET NULL")
    )

    source: Mapped[ImportSource] = relationship(back_populates="services")


class ImportWebhookDelivery(Base):
    """GitHub delivery ids seen, kept a day so a replay is ignored."""

    __tablename__ = "import_webhook_deliveries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True)
