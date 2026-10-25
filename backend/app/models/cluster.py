"""Public cluster (EKS/GKE) lifecycle state."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.project import TargetCloud


class ClusterState(str, enum.Enum):
    UP = "up"
    DOWN = "down"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR = "error"


def _enum(cls):
    # VARCHAR + value storage: no Postgres enum type to migrate.
    return Enum(
        cls,
        native_enum=False,
        length=16,
        values_callable=lambda e: [m.value for m in e],
    )


class Cluster(Base):
    """One row per public cloud; a missing row means the cluster is down."""

    __tablename__ = "cluster_state"

    cloud: Mapped[TargetCloud] = mapped_column(
        _enum(TargetCloud), primary_key=True
    )
    state: Mapped[ClusterState] = mapped_column(
        _enum(ClusterState), nullable=False, default=ClusterState.DOWN
    )
    operation: Mapped[str | None] = mapped_column(
        String(8), nullable=True
    )  # up | down
    step: Mapped[str | None] = mapped_column(String(100), nullable=True)
    done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    started_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    log_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
