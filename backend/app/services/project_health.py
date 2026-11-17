"""
Health of every project in one call: Argo CD Applications (prod) and the
firing alerts per project, read once and cached.
"""

import asyncio
import logging
import time

from app.schemas.project import ProjectHealthRead
from app.services.kube_client import KubeUnavailableError, kube_list
from app.services.links import APPLICATIONS_PATH
from app.services.observability import stores
from app.services.topology import HEALTH_ORDER, SYNC_ORDER, _worst

logger = logging.getLogger(__name__)

# The home polls every 30 s: one read serves every poll in the window.
TTL_SECONDS = 30.0

SELECTOR = "cnp.3istor.com/project,cnp.3istor.com/app,cnp.3istor.com/env=prod"

# (expires at, (argo, alerts))
_cache: tuple[float, tuple[dict | None, dict[str, int] | None]] | None = None


def argo_by_project() -> (
    dict[str, dict[str, tuple[str | None, str | None]]] | None
):
    """{project: {app: (worst health, worst sync)}}; None when unreadable."""
    try:
        items = kube_list(
            APPLICATIONS_PATH,
            {"labelSelector": SELECTOR},
        )
    except KubeUnavailableError as exc:
        logger.warning("Argo CD Applications: %s", exc)
        return None
    components: dict[str, dict[str, tuple[list[str], list[str]]]] = {}
    for item in items:
        labels = item["metadata"].get("labels") or {}
        status = item.get("status") or {}
        health, sync = components.setdefault(
            labels["cnp.3istor.com/project"], {}
        ).setdefault(labels["cnp.3istor.com/app"], ([], []))
        health.append(str((status.get("health") or {}).get("status", "")))
        sync.append(str((status.get("sync") or {}).get("status", "")))
    return {
        project: {
            app: (
                _worst([h.lower() for h in health], HEALTH_ORDER),
                _worst([s.lower() for s in sync], SYNC_ORDER),
            )
            for app, (health, sync) in apps.items()
        }
        for project, apps in components.items()
    }


async def _alerts() -> dict[str, int] | None:
    try:
        return await stores.firing_by_project()
    except (stores.StoreError, stores.NotAvailableError) as exc:
        logger.warning("Alertmanager: %s", exc)
        return None


async def snapshot() -> tuple[dict | None, dict[str, int] | None]:
    """(argo_by_project, firing_by_project), each None when unreadable."""
    global _cache  # pylint: disable=global-statement
    if _cache is not None and _cache[0] > time.monotonic():
        return _cache[1]
    argo, alerts = await asyncio.gather(
        asyncio.to_thread(argo_by_project), _alerts()
    )
    _cache = (time.monotonic() + TTL_SECONDS, (argo, alerts))
    return argo, alerts


def summarize(
    project: str,
    apps: int,
    argo: dict | None,
    alerts: dict[str, int] | None,
) -> ProjectHealthRead:
    """One project's card from the snapshot; None fields when unreadable."""
    found = ((argo or {}).get(project) or {}).values()
    healths = [h for h, _ in found]
    return ProjectHealthRead(
        project=project,
        apps=apps,
        healthy=healths.count("healthy"),
        down=sum(h in ("degraded", "missing") for h in healths),
        health=_worst(healths, HEALTH_ORDER),
        sync=_worst([s for _, s in found], SYNC_ORDER),
        alerts=None if alerts is None else alerts.get(project, 0),
    )
