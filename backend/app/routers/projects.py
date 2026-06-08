"""
Projects Router

Implements the Project > Application hierarchy.

Endpoints:
  GET  /api/projects                       - List projects the current user belongs to
  POST /api/projects                       - Bootstrap a new project via Terraform
  GET  /api/projects/{project_name}/apps   - List applications in a project
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.deployment import Deployment, DeploymentStatus
from app.schemas.deployment import DeploymentRead
from app.schemas.project import ProjectCreate, ProjectCreateResponse, ProjectRead
from app.services.keycloak_service import (
    fetch_user_projects_from_keycloak,
    get_current_user,
    has_project_access,
)
from app.services.project_bootstrap import run_project_bootstrap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Projects"])


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[ProjectRead])
async def list_projects(
    token_payload: Annotated[dict, Depends(get_current_user)],
) -> list[ProjectRead]:
    """
    Return the list of projects the authenticated user belongs to.

    Projects are derived exclusively from Keycloak group membership:
      ``project-<name>-admins`` or ``project-<name>-members``

    The JWT is used only for authentication; group data is fetched fresh
    from the Keycloak Admin API to avoid stale JWT claims.
    """
    user_id: str = token_payload.get("sub", "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not determine user identity from token.",
        )

    projects = fetch_user_projects_from_keycloak(user_id)
    return [ProjectRead(**p) for p in projects]


# ---------------------------------------------------------------------------
# POST /api/projects
# ---------------------------------------------------------------------------


@router.post("/", response_model=ProjectCreateResponse, status_code=202)
async def create_project(
    payload: ProjectCreate,
    background_tasks: BackgroundTasks,
    token_payload: Annotated[dict, Depends(get_current_user)],
) -> ProjectCreateResponse:
    """
    Bootstrap a new project by executing the ``k3s-project-bootstrap`` Terraform module.

    This is an async operation — Terraform runs in a background task.
    The module creates:
    - Keycloak groups: ``project-<name>-admins`` / ``project-<name>-members``
    - Vault policies scoped to the project
    - ArgoCD AppProject

    Required settings (from .env):
    - KEYCLOAK_URL, KEYCLOAK_ADMIN_USERNAME, KEYCLOAK_ADMIN_PASSWORD
    - VAULT_URL, VAULT_TOKEN
    """
    # Validate required settings are present before accepting the request
    missing = []
    if not settings.KEYCLOAK_URL:
        missing.append("KEYCLOAK_URL")
    if not settings.KEYCLOAK_ADMIN_USERNAME:
        missing.append("KEYCLOAK_ADMIN_USERNAME")
    if not settings.KEYCLOAK_ADMIN_PASSWORD:
        missing.append("KEYCLOAK_ADMIN_PASSWORD")
    if not settings.VAULT_URL:
        missing.append("VAULT_URL")
    if not settings.VAULT_TOKEN:
        missing.append("VAULT_TOKEN")

    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Server configuration incomplete. Missing: {', '.join(missing)}",
        )

    background_tasks.add_task(
        run_project_bootstrap,
        project_name=payload.project_name,
    )

    logger.info(
        "Project bootstrap triggered for '%s' by user '%s'",
        payload.project_name,
        token_payload.get("preferred_username", token_payload.get("sub")),
    )

    return ProjectCreateResponse(
        message=(
            f"Project '{payload.project_name}' bootstrap started. "
            "Keycloak groups, Vault policies, and ArgoCD AppProject will be created shortly."
        ),
        project_name=payload.project_name,
    )


# ---------------------------------------------------------------------------
# GET /api/projects/{project_name}/apps
# ---------------------------------------------------------------------------


@router.get("/{project_name}/apps", response_model=list[DeploymentRead])
async def list_project_apps(
    project_name: str,
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> list[DeploymentRead]:
    """
    Return all applications (deployments) belonging to *project_name*.

    Access control: the authenticated user must be a member or admin of the
    project (verified via JWT group claims).
    """
    if not has_project_access(token_payload, project_name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: you are not a member of project '{project_name}'.",
        )

    apps = (
        db.query(Deployment)
        .filter(
            Deployment.project_id == project_name,
            Deployment.status != DeploymentStatus.DELETED,
        )
        .all()
    )

    return apps
