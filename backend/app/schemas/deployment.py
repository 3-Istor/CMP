import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from app.models.deployment import DeploymentStatus, ProviderType


class DeploymentCreate(BaseModel):
    name: str
    template_id: str
    provider_type: ProviderType = ProviderType.LEGACY_HYBRID
    project_id: str | None = None
    app_config: dict[str, Any] = {}


# Step messages written before the emoji cleanup (CMP#145) are still in the database.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]+\\s?"
)


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

    @field_validator("step_message")
    @classmethod
    def _drop_emojis(cls, value: str) -> str:
        return _EMOJI.sub("", value).strip()

    # Multi-provider fields (Phase 3)
    provider_type: ProviderType
    project_id: str | None
    github_repo_url: str | None
    argocd_app_name: str | None
    k8s_namespace: str | None
    created_via: str | None = None

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
