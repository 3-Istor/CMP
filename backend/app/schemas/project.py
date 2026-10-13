"""
Pydantic schemas for Projects API.
"""

from pydantic import BaseModel, Field

from app.models.project import ProjectStatus, TargetCloud


class ProjectRead(BaseModel):
    """A project the current user has access to."""

    name: str = Field(
        ..., description="Unique project identifier (lowercase, kebab-case)"
    )
    role: str = Field(
        ..., description="User role in this project: 'admin' or 'member'"
    )
    target_cloud: TargetCloud = Field(
        default=TargetCloud.ONPREM,
        description=(
            "The cloud this project runs on. Mirrored from the Git registry; "
            "projects created before the multicloud chantier read back as 'onprem'."
        ),
    )
    status: ProjectStatus = Field(
        default=ProjectStatus.ACTIVE,
        description=(
            "Lifecycle state of the project. 'provisioning' and 'decommissioning' "
            "are transient; 'failed' means the last bootstrap or teardown stopped "
            "and step_message says why."
        ),
    )
    step_message: str | None = Field(
        default=None,
        description="What the bootstrap or teardown is doing now, or why it failed.",
    )
    is_accessible: bool = Field(
        default=True,
        description=(
            "False while the project's Keycloak groups do not exist, e.g. a "
            "bootstrap that has not finished or failed. Its page cannot load."
        ),
    )


class ProjectCreate(BaseModel):
    """Payload for creating a new project via Terraform bootstrap."""

    project_name: str = Field(
        ...,
        min_length=2,
        max_length=40,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        description=(
            "Lowercase, kebab-case project name. "
            "Used as a Keycloak group prefix, Vault policy name, and ArgoCD AppProject name."
        ),
    )
    target_cloud: TargetCloud = Field(
        default=TargetCloud.ONPREM,
        description=(
            "Which cloud the project's workloads run on. Immutable after creation: "
            "changing it is a re-create, not a move. Defaults to on-prem so existing "
            "clients keep working unchanged."
        ),
    )


class ProjectCreateResponse(BaseModel):
    """Immediate response after triggering project bootstrap."""

    message: str
    project_name: str
    target_cloud: TargetCloud
    status: str = "bootstrapping"
