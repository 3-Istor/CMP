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
    api_reachable,
    kube_get,
    kube_list,
    kube_merge_patch,
)
from app.services.release.route_reconciler import _sync_in_flight

logger = logging.getLogger(__name__)

PUBLIC_CLOUDS = (TargetCloud.AWS, TargetCloud.GCP)
APPS_PATH = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"
APP_PATH = APPS_PATH + "/wireguard-{}"
PLATFORM_PATH = APPS_PATH + "/platform-{}"
# Clusters and Placement show this state; a refresh probes both clouds at
# once, up to PROBE_TIMEOUT (3 s) each when powered off, in the background.
CACHE_TTL = 15
GUARD_RETRIES = 4  # unknown is re-read every GUARD_DELAY s, ~20 s in all
GUARD_DELAY = 5

_HEALTH = {"Healthy": "up", "Progressing": "starting"}
_REACHABLE = ("up", "degraded", "starting")
_cache: dict[TargetCloud, str] = {}
_cached_at: float | None = None
# Clouds that came up and still wait for their resync (until platform-<cloud> is ready).
_pending_resync: set[TargetCloud] = set()
# Clouds that came back and still wait for the cleanup of the moves forced away from them.
_pending_cleanup: set[TargetCloud] = set()
_refresh_task: asyncio.Task | None = None


def fresh_state(cloud: TargetCloud) -> str:
    """up | degraded | starting | down | unknown.

    down: the kube API does not answer; unknown: wireguard-<cloud> absent or unreadable;
    otherwise the Application health: Healthy up, Progressing starting, else degraded.
    """
    try:
        app = kube_get(APP_PATH.format(cloud.value))
    except (KubeUnavailableError, ValueError):
        return "unknown"
    if app is None:
        return "unknown"
    if not api_reachable(cloud):
        return "down"
    status = app.get("status") or {}
    result = _HEALTH.get(
        (status.get("health") or {}).get("status"), "degraded"
    )
    # Argo CD keeps a stale Healthy when it cannot reach the cluster.
    errors = any(
        str(c.get("type", "")).endswith("Error")
        for c in status.get("conditions") or []
    )
    if result == "up" and (
        (status.get("sync") or {}).get("status") == "Unknown" or errors
    ):
        return "degraded"
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


def platform_ready(cloud: TargetCloud) -> bool:
    """platform-<cloud> Synced and Healthy; a cluster without that Application counts as ready."""
    app = kube_get(PLATFORM_PATH.format(cloud.value))
    if app is None:
        return True
    status = app.get("status") or {}
    return (status.get("sync") or {}).get("status") == "Synced" and (
        status.get("health") or {}
    ).get("status") == "Healthy"


async def _cleanup_returned(cloud: TargetCloud) -> None:
    # Imported here: steps imports this module.
    from app.services.moves.steps import (  # pylint: disable=cyclic-import
        cleanup_returned,
    )

    try:
        done = await run_in_threadpool(cleanup_returned, cloud)
        _pending_cleanup.discard(cloud)
        if done:
            logger.info("%s is back, cleaned up %s", cloud.value, done)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("%s is back, cleanup failed: %s", cloud.value, exc)


async def _refresh() -> None:
    global _cache, _cached_at
    before = _cache
    states = await asyncio.gather(
        *(run_in_threadpool(fresh_state, c) for c in PUBLIC_CLOUDS)
    )
    _cache = dict(zip(PUBLIC_CLOUDS, states))
    _cached_at = time.monotonic()
    for cloud in PUBLIC_CLOUDS:
        if _cache[cloud] in _REACHABLE:
            if before.get(cloud, "unknown") in ("down", "unknown"):
                _pending_cleanup.add(cloud)
        else:
            _pending_cleanup.discard(cloud)
        if cloud in _pending_cleanup:
            await _cleanup_returned(cloud)
        if _cache[cloud] != "up":
            _pending_resync.discard(cloud)
            continue
        if before.get(cloud) != "up":
            _pending_resync.add(cloud)
        if cloud not in _pending_resync:
            continue
        try:
            # Apps synced before the platform's CRDs exist fail and stay failed.
            if not await run_in_threadpool(platform_ready, cloud):
                continue
            names = await run_in_threadpool(resync, cloud)
            _pending_resync.discard(cloud)
            logger.info("%s is up, resynced %s", cloud.value, names)
        except (KubeUnavailableError, ValueError) as exc:
            logger.warning("%s is up, resync failed: %s", cloud.value, exc)
    # Imported here: moves import this module.
    from app.services.moves import footprint  # pylint: disable=C0415

    # On-prem is up whenever the CMP answers.
    up = [TargetCloud.ONPREM] + [c for c in PUBLIC_CLOUDS if _cache[c] == "up"]
    await run_in_threadpool(footprint.refresh_due, up)


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
    if state == "degraded":
        raise HTTPException(
            409, f"The {name} cluster is degraded; retry in a few minutes."
        )
    if state == "starting":
        raise HTTPException(
            409, f"The {name} cluster is starting; retry in a few minutes."
        )
    if state == "unknown":
        raise HTTPException(
            503, f"The {name} cluster state is unknown; retry later."
        )


def pick_public_cloud(
    states: dict[TargetCloud, str], last: TargetCloud | None
) -> TargetCloud:
    """Cloud for a new public project: an up one, not ``last`` if both are up; 409 if none is."""
    up = [c for c in PUBLIC_CLOUDS if states.get(c) == "up"]
    if not up:
        seen = ", ".join(
            f"{c.value.upper()}: {states.get(c)}" for c in PUBLIC_CLOUDS
        )
        raise HTTPException(
            409,
            f"No public cloud is up ({seen}); retry later or choose on-prem.",
        )
    # ponytail: alternation until OpenCost average total cost exists, then pick the cheapest up cloud.
    return next((c for c in up if c != last), up[0])
