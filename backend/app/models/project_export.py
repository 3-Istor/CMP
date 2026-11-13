from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProjectExport(Base):
    """
    One export of a project's data (EU Data Act Art. 25): an archive kept in
    the export bucket until ``expires_at``, then deleted. Times are naive UTC,
    as SQLite stores them.
    """

    __tablename__ = "project_exports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project: Mapped[str] = mapped_column(String(100), index=True)
    requested_by: Mapped[str] = mapped_column(String(255))
    include_secret_values: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending, running, ready, partial, failed, expired
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    object_key: Mapped[str | None] = mapped_column(String(255))
    size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    # JSON list of {"source", "error"}: what could not be exported.
    problems: Mapped[str] = mapped_column(Text, default="[]")
