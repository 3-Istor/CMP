"""
Useful links of a project and of an app, filtered by the caller's role.

Dedicated endpoints rather than a field on the detail responses: the app
shape is shared with lists and creation, and building the links of an app
reads the Argo CD Applications from the cluster, a cost no list should pay.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, DeploymentStatus
from app.models.environment import AppEnvironment
from app.services import alerts_grafana as grafana
from app.services import links
from app.services.authz import RequireGuest, assert_project_role, is_cnp_admin
from app.services.keycloak_service import get_current_user

projects = APIRouter(prefix="/projects", tags=["Links"])
deployments = APIRouter(prefix="/deployments", tags=["Links"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


@projects.get("/{project_name}/links", response_model=list[links.Link])
async def get_project_links(
    ctx: RequireGuest, db: Session = Depends(get_db)
) -> list[links.Link]:
    """Access control: any role on the project, the list shrinks with the role."""
    try:
        org = await grafana.org_id(ctx.project_name)
    except Exception:  # pylint: disable=broad-exception-caught
        # No org yet (Terraform creates it) or Grafana unreachable.
        org = None
    apps = (
        db.query(Deployment)
        .filter(
            Deployment.project_id == ctx.project_name,
            Deployment.status != DeploymentStatus.DELETED,
        )
        .all()
    )
    has_staging = (
        db.query(AppEnvironment)
        .filter(
            AppEnvironment.deployment_id.in_([app.id for app in apps]),
            AppEnvironment.name == "staging",
            AppEnvironment.state == "ready",
        )
        .first()
        is not None
    )
    return links.project_links(
        ctx.project_name, ctx.role, apps, org, has_staging
    )


@deployments.get("/{deployment_id}/links", response_model=list[links.Link])
async def get_app_links(
    deployment_id: int, token: CurrentUser, db: Session = Depends(get_db)
) -> list[links.Link]:
    """Access control: any role on the app's project."""
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if is_cnp_admin(token):
        role = ProjectRole.ADMIN
    elif not deployment.project_id:
        raise HTTPException(
            status_code=403,
            detail="This application is not attached to a project.",
        )
    else:
        role = assert_project_role(
            db, token, deployment.project_id, ProjectRole.GUEST
        ).role
    return await run_in_threadpool(links.app_links, deployment, role)
