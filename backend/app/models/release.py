from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReleaseEvent(Base):
    """
    One attempt at rolling out a revision of an app, as the CMP saw it.

    Argo Rollouts keeps no history of past releases nor of who pushed them
    along, so this row is the only record of both.
    """

    __tablename__ = "release_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(Integer, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    image: Mapped[str | None] = mapped_column(String(512))
    strategy: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    outcome: Mapped[str] = mapped_column(String(16), default="running")
    forced: Mapped[bool] = mapped_column(Boolean, default=False)
    reached_step: Mapped[int | None] = mapped_column(Integer)
    steps: Mapped[list | None] = mapped_column(JSON)
    actors: Mapped[list | None] = mapped_column(JSON)
