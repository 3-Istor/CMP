"""Persistent, single-use permission and progress record for repository imports."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RepositoryImport(Base):
    __tablename__ = "repository_imports"
    __table_args__ = (
        UniqueConstraint("app_name"),
        UniqueConstraint("repository_url"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    app_name: Mapped[str] = mapped_column(String(63), nullable=False)
    repository_url: Mapped[str] = mapped_column(String(255), nullable=False)
    user_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    installation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued"
    )
    run_id: Mapped[str | None] = mapped_column(String(100))
    source_sha: Mapped[str | None] = mapped_column(String(40))
    pull_request: Mapped[int | None] = mapped_column(Integer)
    deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployments.id"), unique=True
    )
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    message: Mapped[str] = mapped_column(
        String(255), nullable=False, default="En attente de la CI"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
