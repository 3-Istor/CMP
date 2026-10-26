"""Read-only state of the public clusters, from Argo CD's wireguard-<cloud> Applications."""

import asyncio
import time

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.models.project import TargetCloud
from app.services.kube_client import KubeUnavailableError, kube_get

PUBLIC_CLOUDS = (TargetCloud.AWS, TargetCloud.GCP)
APP_PATH = (
    "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/wireguard-{}"
)
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
    health = (app.get("status") or {}).get("health") or {}
    return _HEALTH.get(health.get("status"), "down")


async def _refresh() -> None:
    global _cache, _cached_at
    _cache = {
        c: await run_in_threadpool(fresh_state, c) for c in PUBLIC_CLOUDS
    }
    _cached_at = time.monotonic()


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
