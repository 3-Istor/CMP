"""Live placement of projects across clusters, for platform admins."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.services import placement
from app.services.authz import is_cnp_admin
from app.services.clusters.state import cluster_states, project_cloud
from app.services.keycloak_service import get_current_user

router = APIRouter(tags=["placement"])


def require_platform_admin(
    token: Annotated[dict, Depends(get_current_user)],
) -> dict:
    if not is_cnp_admin(token):
        raise HTTPException(403, "Placement is for platform admins.")
    return token


@router.get("/projects/{project_name}/placement")
async def project_placement(
    project_name: str,
    _: Annotated[dict, Depends(require_platform_admin)],
    db: Session = Depends(get_db),
) -> dict:
    current = project_cloud(db, project_name)
    await cluster_states()  # starts a background refresh when stale
    return await run_in_threadpool(
        placement.project_placement, project_name, current
    )


@router.get("/placement")
async def platform_placement(
    _: Annotated[dict, Depends(require_platform_admin)],
    db: Session = Depends(get_db),
) -> dict:
    counts = await run_in_threadpool(placement.visible_app_counts, db)
    return await placement.platform_placement(counts)
