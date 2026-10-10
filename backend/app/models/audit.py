from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditEvent(Base):
    """
    One change requested through the CMP. Commits it makes are signed by the
    GitHub App and applied by Argo CD, so this row is the only place the person
    behind a change is recorded.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    project: Mapped[str | None] = mapped_column(String(100), index=True)
    app: Mapped[str | None] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(255), default="")
    outcome: Mapped[str] = mapped_column(String(16))
    status_code: Mapped[int] = mapped_column(Integer)
    source_ip: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[str] = mapped_column(Text, default="{}")
