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
    template_name: str | None
    template_icon: str | None
    template_category: str | None
    status: DeploymentStatus
    step_message: str
    terraform_outputs: str | None  # JSON string
    resource_count: int | None
    created_at: datetime
    updated_at: datetime

    @property
    def outputs(self) -> dict[str, Any]:
        """Parse Terraform outputs from JSON."""
        if not self.terraform_outputs:
            return {}
        try:
            import json
            return json.loads(self.terraform_outputs)
        except Exception:
            return {}


class DeploymentStatusUpdate(BaseModel):
    status: DeploymentStatus
    step_message: str
