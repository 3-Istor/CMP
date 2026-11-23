from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProjectFootprint(Base):
    """Last pod requests measured for one app of a project on one cloud,
    kept while the cluster is down."""

    __tablename__ = "project_footprints"

    project: Mapped[str] = mapped_column(String(255), primary_key=True)
    # "system" is the project's system namespace.
    app: Mapped[str] = mapped_column(String(255), primary_key=True)
    cloud: Mapped[str] = mapped_column(String(16), primary_key=True)
    cpu: Mapped[float] = mapped_column(Float)
    memory_bytes: Mapped[float] = mapped_column(Float)
    pods: Mapped[int] = mapped_column(Integer)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
