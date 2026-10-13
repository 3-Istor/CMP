from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AlertSetting(Base):
    """A catalogue alert switched on or off for a project or one of its apps."""

    __tablename__ = "alert_settings"
    __table_args__ = (UniqueConstraint("project", "app", "alert_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(100), index=True)
    # "" for the whole project.
    app: Mapped[str] = mapped_column(String(100), default="")
    alert_id: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[str] = mapped_column(Text, default="{}")
    # The namespaces the provisioned rule covers, to notice new apps.
    namespaces: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
