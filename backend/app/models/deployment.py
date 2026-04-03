import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeploymentStatus(str, enum.Enum):
    PENDING = "pending"
    DEPLOYING_OPENSTACK = "deploying_openstack"
    DEPLOYING_AWS = "deploying_aws"
    RUNNING = "running"
    DEGRADED = "degraded"
    ROLLING_BACK = "rolling_back"
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

    # OpenStack resource IDs
    os_vm_db1_id: Mapped[str | None] = mapped_column(String(100))
    os_vm_db2_id: Mapped[str | None] = mapped_column(String(100))
    os_vm_db1_ip: Mapped[str | None] = mapped_column(String(50))
    os_vm_db2_ip: Mapped[str | None] = mapped_column(String(50))

    # AWS resource IDs
    aws_asg_name: Mapped[str | None] = mapped_column(String(100))
    aws_alb_dns: Mapped[str | None] = mapped_column(String(255))
    aws_instance_ids: Mapped[str | None] = mapped_column(
        Text
    )  # JSON list of IDs

    # Metadata
    app_config: Mapped[str | None] = mapped_column(
        Text
    )  # JSON of user-provided config
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
