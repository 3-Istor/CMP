"""
Pydantic schemas for Projects API.
"""

from pydantic import BaseModel, Field


class ProjectRead(BaseModel):
    """A project the current user has access to."""

    name: str = Field(..., description="Unique project identifier (lowercase, kebab-case)")
    role: str = Field(..., description="User role in this project: 'admin' or 'member'")


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


class ProjectCreateResponse(BaseModel):
    """Immediate response after triggering project bootstrap."""

    message: str
    project_name: str
    status: str = "bootstrapping"
