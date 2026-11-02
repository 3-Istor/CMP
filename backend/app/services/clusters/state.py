"""State of the public clusters, from Argo CD's wireguard-<cloud> Applications."""

import asyncio
import logging
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.project import Project, TargetCloud
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_get,
    kube_list,
    kube_merge_patch,
)
from app.services.release.route_reconciler import _sync_in_flight

logger = logging.getLogger(__name__)

PUBLIC_CLOUDS = (TargetCloud.AWS, TargetCloud.GCP)
APPS_PATH = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"
APP_PATH = APPS_PATH + "/wireguard-{}"
CACHE_TTL = 60
GUARD_RETRIES = 4  # unknown is re-read every GUARD_DELAY s, ~20 s in all
GUARD_DELAY = 5

_HEALTH = {"Healthy": "up", "Progressing": "starting"}
_cache: dict[TargetCloud, str] = {}
_cached_at: float | None = None
_refresh_task: asyncio.Task | None = None


def fresh_state(cloud: TargetCloud) -> str:
    """up | starting | down from the Application health; unknown if absent or unreadable."""
    try:
        app = kube_get(APP_PATH.format(cloud.value))
    except (KubeUnavailableError, ValueError):
        return "unknown"
    if app is None:
        return "unknown"
    status = app.get("status") or {}
    result = _HEALTH.get((status.get("health") or {}).get("status"), "down")
    # Argo CD keeps a stale Healthy when the cluster is unreachable.
    errors = any(
        str(c.get("type", "")).endswith("Error")
        for c in status.get("conditions") or []
    )
    if result == "up" and (
        (status.get("sync") or {}).get("status") == "Unknown" or errors
    ):
        return "down"
    return result


def cached_state(cloud: TargetCloud) -> str:
    """Last state read by the background refresh; unknown before the first one."""
    return _cache.get(cloud, "unknown")


def project_cloud(db: Session, project_name: str | None) -> TargetCloud:
    """The cluster a project runs on; on-prem when the project is unknown."""
    cloud = (
        db.query(Project.target_cloud)
        .filter(Project.project_name == project_name)
        .scalar()
    )
    return cloud or TargetCloud.ONPREM


async def not_connected_response(
    _request: Request, exc: ClusterNotConnectedError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "cluster_not_connected", "cloud": exc.cloud},
    )


def resync(cloud: TargetCloud) -> list[str]:
    """Sync the cloud's unsynced or failed Applications, return their names.

    Their retries are finite and auto-sync never re-attempts a failed revision,
    so a sync that failed while the cluster was down would stay failed.
    """
    synced = []
    for app in kube_list(APPS_PATH):
        spec, status = app.get("spec", {}), app.get("status", {})
        failed = status.get("operationState", {}).get("phase") in (
            "Failed",
            "Error",
        )
        if (
            spec.get("destination", {}).get("name") != cloud.value
            # An operation on a deleting Application would hold its deletion.
            or "deletionTimestamp" in app["metadata"]
            or _sync_in_flight(app)
            or (
                status.get("sync", {}).get("status") == "Synced" and not failed
            )
        ):
            continue
        policy = spec.get("syncPolicy", {})
        # What auto-sync would set: the Application's sync options and retry.
        kube_merge_patch(
            f"{APPS_PATH}/{app['metadata']['name']}",
            {
                "operation": {
                    "initiatedBy": {"username": "cmp-cluster-state"},
                    "sync": {"syncOptions": policy.get("syncOptions", [])},
                    "retry": policy.get("retry", {}),
                }
            },
        )
        synced.append(app["metadata"]["name"])
    return synced


async def _refresh() -> None:
    global _cache, _cached_at
    before = _cache
    _cache = {
        c: await run_in_threadpool(fresh_state, c) for c in PUBLIC_CLOUDS
    }
    _cached_at = time.monotonic()
    for cloud in PUBLIC_CLOUDS:
        if _cache[cloud] != "up" or before.get(cloud) == "up":
            continue
        try:
            names = await run_in_threadpool(resync, cloud)
            logger.info("%s is up, resynced %s", cloud.value, names)
        except KubeUnavailableError as exc:
            logger.warning("%s is up, resync failed: %s", cloud.value, exc)


async def cluster_states() -> dict[TargetCloud, str]:
    """Cached states, never awaiting a read: a stale cache is refreshed in the background."""
    global _refresh_task
    stale = _cached_at is None or time.monotonic() - _cached_at >= CACHE_TTL
    if stale and (_refresh_task is None or _refresh_task.done()):
        _refresh_task = asyncio.create_task(_refresh())
    return {c: _cache.get(c, "unknown") for c in PUBLIC_CLOUDS}


async def ensure_cluster_up(cloud: TargetCloud) -> None:
    """409/503 unless the cloud's cluster is up (read fresh); on-prem always passes."""
    if cloud not in PUBLIC_CLOUDS:
        return
    state = await run_in_threadpool(fresh_state, cloud)
    for _ in range(GUARD_RETRIES):
        if state != "unknown":
            break
        await asyncio.sleep(GUARD_DELAY)
        state = await run_in_threadpool(fresh_state, cloud)
    name = cloud.value.upper()
    if state == "down":
        raise HTTPException(
            409,
            f"The {name} cluster is down; deployments resume once it is up.",
        )
    if state == "starting":
        raise HTTPException(
            409, f"The {name} cluster is starting; retry in a few minutes."
        )
    if state == "unknown":
        raise HTTPException(
            503, f"The {name} cluster state is unknown; retry later."
        )
