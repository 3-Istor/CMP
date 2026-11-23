"""What each project's apps really request on a cluster, kept while it is up
so a move can be sized when the cluster is down."""

import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.project import TargetCloud
from app.models.project_footprint import ProjectFootprint
from app.services.environments import layout
from app.services.kube_client import kube_list
from app.services.placement import PROJECT_LABEL, quantity_bytes

logger = logging.getLogger(__name__)

# Requests only change on a deploy or a scale: 5 min bounds how stale the
# last value is when a cluster drops, for two list calls per cluster.
EVERY = 300
_last: dict[TargetCloud, float] = {}


def pod_requests(pods: list[dict]) -> tuple[float, float, int]:
    """(CPU, memory bytes, pods) requested by the pods that still hold room."""
    cpu = memory = 0.0
    count = 0
    for pod in pods:
        # A finished pod holds nothing.
        if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        count += 1
        for c in (pod.get("spec") or {}).get("containers") or []:
            asked = (c.get("resources") or {}).get("requests") or {}
            cpu += quantity_bytes(asked.get("cpu", "0"))
            memory += quantity_bytes(asked.get("memory", "0"))
    return cpu, memory, count


def app_of(project: str, namespace: str) -> str:
    """<project>-<app>[-stg] -> app; the system namespace is "system"."""
    app = namespace.removeprefix(f"{project}-")
    return app.removesuffix(layout.SUFFIX["staging"])


def measure(
    cloud: TargetCloud,
) -> tuple[set[str], dict[tuple[str, str], tuple[float, float, int]]]:
    """(projects with a namespace, requests per (project, app) with pods)
    on *cloud*: two list calls whatever the number of projects."""
    owner = {
        ns["metadata"]["name"]: ns["metadata"]["labels"][PROJECT_LABEL]
        for ns in kube_list(
            "/api/v1/namespaces", {"labelSelector": PROJECT_LABEL}, cloud=cloud
        )
    }
    by_app: dict[tuple[str, str], list[dict]] = {}
    for pod in kube_list("/api/v1/pods", cloud=cloud):
        ns = pod["metadata"]["namespace"]
        if ns in owner:
            key = (owner[ns], app_of(owner[ns], ns))
            by_app.setdefault(key, []).append(pod)
    return set(owner.values()), {
        key: pod_requests(pods) for key, pods in by_app.items()
    }


def refresh(db: Session, cloud: TargetCloud, now: datetime | None = None):
    """Upsert what *cloud* shows now. A cluster that cannot be read raises
    before anything is written: the last values stay."""
    projects, measured = measure(cloud)
    now = now or datetime.now(timezone.utc)
    for (project, app), (cpu, memory, pods) in measured.items():
        db.merge(
            ProjectFootprint(
                project=project,
                app=app,
                cloud=cloud.value,
                cpu=cpu,
                memory_bytes=memory,
                pods=pods,
                measured_at=now,
            )
        )
    # A project that left the cluster holds nothing there any more.
    db.query(ProjectFootprint).filter(
        ProjectFootprint.cloud == cloud.value,
        ProjectFootprint.project.not_in(projects),
    ).delete(synchronize_session=False)
    db.commit()


def refresh_due(clouds: list[TargetCloud]) -> None:
    """Refresh the clouds not measured for EVERY seconds; one failing does not
    stop the others."""
    # Imported here: the module is loaded by the app before the database.
    from app.core.database import SessionLocal  # pylint: disable=C0415

    for cloud in clouds:
        if time.monotonic() - _last.get(cloud, -EVERY) < EVERY:
            continue
        _last[cloud] = time.monotonic()
        try:
            with SessionLocal() as db:
                refresh(db, cloud)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "footprint of %s not refreshed: %s", cloud.value, exc
            )


def cached(
    db: Session, project: str, cloud: TargetCloud
) -> tuple[tuple[float, float], datetime] | None:
    """((CPU, memory bytes), oldest measure) of the project's apps on *cloud*."""
    rows = (
        db.query(ProjectFootprint)
        .filter(
            ProjectFootprint.project == project,
            ProjectFootprint.cloud == cloud.value,
        )
        .all()
    )
    if not rows:
        return None
    at = min(r.measured_at for r in rows)
    if at.tzinfo is None:  # SQLite drops the zone
        at = at.replace(tzinfo=timezone.utc)
    return (
        sum(r.cpu for r in rows),
        sum(r.memory_bytes for r in rows),
    ), at
