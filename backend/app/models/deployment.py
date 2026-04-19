import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeploymentStatus(str, enum.Enum):
    PENDING = "pending"
    INITIALIZING = "initializing"
    PLANNING = "planning"
    DEPLOYING = "deploying"
    RUNNING = "running"
    DEGRADED = "degraded"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    template_id: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[DeploymentStatus] = mapped_column(
        Enum(DeploymentStatus), default=DeploymentStatus.PENDING
    )
    step_message: Mapped[str] = mapped_column(
        String(255), default="Initializing..."
    )

    # Terraform outputs (JSON)
    # Stores all outputs from Terraform (IPs, URLs, resource IDs, etc.)
    terraform_outputs: Mapped[str | None] = mapped_column(
        Text
    )  # JSON of Terraform outputs

    # Terraform state tracking
    terraform_state_path: Mapped[str | None] = mapped_column(String(255))
    resource_count: Mapped[int | None] = mapped_column(Integer, default=0)

    # Metadata
    app_config: Mapped[str | None] = mapped_column(
        Text
    )  # JSON of user-provided config

    # Template metadata
    template_name: Mapped[str | None] = mapped_column(String(100))
    template_icon: Mapped[str | None] = mapped_column(String(50))
    template_category: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )
