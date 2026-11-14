from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SloSetting(Base):
    """An app's SLO targets set by its owner; no row means the defaults."""

    __tablename__ = "slo_settings"
    __table_args__ = (UniqueConstraint("project", "app"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(100), index=True)
    app: Mapped[str] = mapped_column(String(100))
    # Percent of good requests over the SLO period.
    availability: Mapped[float] = mapped_column(Float)
    latency: Mapped[float] = mapped_column(Float)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )


class TraceTenant(Base):
    """
    A project's VictoriaTraces tenant (AccountID = id, ProjectID 0). Rows
    are retired, never deleted, so an id is never given to another project,
    even one later created under the same name.
    """

    __tablename__ = "trace_tenants"
    __table_args__ = (
        Index(
            "ux_trace_tenants_active_project",
            "project",
            unique=True,
            sqlite_where=text("retired_at IS NULL"),
            postgresql_where=text("retired_at IS NULL"),
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # pylint: disable=not-callable
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
