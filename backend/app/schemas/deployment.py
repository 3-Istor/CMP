from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.deployment import DeploymentStatus


class DeploymentCreate(BaseModel):
    name: str
    template_id: str
    app_config: dict[str, Any] = {}


class DeploymentRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    template_id: str
    status: DeploymentStatus
    step_message: str
    os_vm_db1_ip: str | None
    os_vm_db2_ip: str | None
    aws_alb_dns: str | None
    aws_asg_name: str | None
    created_at: datetime
    updated_at: datetime


class DeploymentStatusUpdate(BaseModel):
    status: DeploymentStatus
    step_message: str
