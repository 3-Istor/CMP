"""Start and stop the public clusters (EKS/GKE). Restricted to CNP admins."""

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.models.cluster import Cluster, ClusterState
from app.models.project import TargetCloud
from app.services.authz import is_cnp_admin
from app.services.clusters.ops import affected_apps, run_operation
from app.services.clusters.recovery import _tasks
from app.services.clusters.state import PUBLIC_CLOUDS, TransitionError, begin
from app.services.keycloak_service import get_current_user
from app.services.monitoring_service import ClusterStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clusters", tags=["Clusters"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _admin_cloud(token: dict, cloud: str) -> TargetCloud:
    if not is_cnp_admin(token):
        raise HTTPException(403, "CNP admin role required")
    try:
        parsed = TargetCloud(cloud)
    except ValueError:
        parsed = None
    if parsed not in PUBLIC_CLOUDS:
        raise HTTPException(404, f"Unknown cluster '{cloud}'")
    return parsed


def _start(db: Session, token: dict, cloud: TargetCloud, operation: str) -> ClusterStatus:
    user = token.get("preferred_username") or token.get("sub", "")
    try:
        row = begin(db, cloud, operation, user)
    except (TransitionError, IntegrityError) as exc:
        db.rollback()  # the pending row must not leak into the session
        raise HTTPException(409, str(exc) if isinstance(exc, TransitionError)
                            else "Another operation just started") from exc
    task = asyncio.create_task(run_operation(cloud, operation))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return ClusterStatus.model_validate(row)


@router.post("/{cloud}/up", status_code=202, response_model=ClusterStatus)
async def cluster_up(cloud: str, token: CurrentUser, db: Session = Depends(get_db)):
    return _start(db, token, _admin_cloud(token, cloud), "up")


@router.get("/{cloud}/affected-apps")
async def cluster_affected_apps(cloud: str, token: CurrentUser, db: Session = Depends(get_db)):
    return {"affected_apps": affected_apps(db, _admin_cloud(token, cloud))}


@router.post("/{cloud}/down", status_code=202, response_model=ClusterStatus)
async def cluster_down(cloud: str, token: CurrentUser, db: Session = Depends(get_db)):
    return _start(db, token, _admin_cloud(token, cloud), "down")


def _busy(cloud: TargetCloud) -> bool:
    with SessionLocal() as db:
        row = db.get(Cluster, cloud)
        return bool(row) and row.state in (ClusterState.STARTING, ClusterState.STOPPING)


@router.get("/{cloud}/logs/stream")
async def stream_cluster_logs(
    cloud: str, request: Request, token: CurrentUser, db: Session = Depends(get_db)
):
    parsed = _admin_cloud(token, cloud)
    row = db.get(Cluster, parsed)
    log_file = Path(row.log_path) if row and row.log_path else None

    async def log_generator():
        if log_file is None:
            return
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.touch()
        with open(log_file, "r", encoding="utf-8") as f:
            while not await request.is_disconnected():
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                    continue
                if not await run_in_threadpool(_busy, parsed):
                    for line in f:  # drain what was written since the last read
                        yield f"data: {line.rstrip()}\n\n"
                    break
                await asyncio.sleep(0.5)

    return StreamingResponse(log_generator(), media_type="text/event-stream")
