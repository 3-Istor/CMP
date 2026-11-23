"""Mirror of the hosted agents declared in the project records."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiAgent(Base):
    """
    One hosted agent, for fast lists and the admin inventory. Git stays the
    source: the rights in the project record, the declaration in the values
    file of the app repository. The CMP rewrites a row whenever it reads or
    writes either.
    """

    __tablename__ = "ai_agents"
    __table_args__ = (UniqueConstraint("project", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(63))
    template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    source: Mapped[str] = mapped_column(String(8), default="cmp")
    repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    values_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rights: Mapped[dict] = mapped_column(JSON, default=dict)
    declaration: Mapped[dict] = mapped_column(JSON, default=dict)
    pull_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    settings_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    settings_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    credentials: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
