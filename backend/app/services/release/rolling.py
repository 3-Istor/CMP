"""
Release history of apps that roll out with a plain Deployment.

Argo Rollouts keeps one ReplicaSet per revision of a canary app; a Deployment
does the same under ``deployment.kubernetes.io/revision``, which is all there
is to record for a rolling update.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.models.release import ReleaseEvent
from app.services.environments import gitops, layout, observe
from app.services.kube_client import kube_get, kube_list

REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
# Set when a rollback reuses an old ReplicaSet under a new revision number.
REUSED_ANNOTATION = "deployment.kubernetes.io/revision-history"
# What the Progressing condition's reason says about the newest revision.
OUTCOMES = {
    "ProgressDeadlineExceeded": "failed",
    "NewReplicaSetAvailable": "deployed",
}


@dataclass
class RollingRevision:
    environment: str
    component: str
    revision: int
    image: str | None
    started_at: datetime
    outcome: str


def _revision_of(replica_set: dict) -> int | None:
    meta = replica_set.get("metadata", {})
    owned = any(
        ref.get("kind") == "Deployment"
        for ref in meta.get("ownerReferences") or []
    )
    raw = (meta.get("annotations") or {}).get(REVISION_ANNOTATION)
    return int(raw) if owned and raw else None


def _image_of(replica_set: dict) -> str | None:
    pod_spec = replica_set.get("spec", {}).get("template", {}).get("spec", {})
    for container in pod_spec.get("containers") or []:
        if container.get("name") == observe.CONTAINER:
            return container.get("image")
    return None


def _started_at(replica_set: dict) -> datetime:
    meta = replica_set["metadata"]
    if REUSED_ANNOTATION in (meta.get("annotations") or {}):
        # creationTimestamp is the first use of the template, not this one.
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(meta["creationTimestamp"])


def _newest_outcome(deployment_obj: dict | None) -> str:
    deployment_obj = deployment_obj or {}
    status = deployment_obj.get("status", {})
    generation = deployment_obj.get("metadata", {}).get("generation", 0)
    # The conditions still describe the previous generation until observed.
    if status.get("observedGeneration", 0) < generation:
        return "running"
    for condition in status.get("conditions") or []:
        if condition.get("type") == "Progressing":
            return OUTCOMES.get(condition.get("reason"), "running")
    return "running"


def revisions_of(
    environment: str,
    component: str,
    replica_sets: list[dict],
    deployment_obj: dict | None,
) -> list[RollingRevision]:
    """One revision per ReplicaSet of a Deployment, oldest first."""
    found = [
        (revision, rs)
        for rs in replica_sets
        if (revision := _revision_of(rs)) is not None
    ]
    found.sort(key=lambda item: item[0])
    newest = _newest_outcome(deployment_obj)
    return [
        RollingRevision(
            environment=environment,
            component=component,
            revision=revision,
            image=_image_of(rs),
            started_at=_started_at(rs),
            outcome=newest if i == len(found) - 1 else "deployed",
        )
        for i, (revision, rs) in enumerate(found)
    ]


def read_rolling(
    deployment: Deployment, cloud: TargetCloud, environments: list[str]
) -> list[RollingRevision]:
    """
    Raises:
        KubeUnavailableError: The cluster cannot be read.
        ClusterNotConnectedError: The project's public cloud is not up.
    """
    project, app = deployment.project_id or "", deployment.name
    found = []
    for environment in environments:
        namespace = layout.namespace(project, app, environment)
        for component in layout.components(gitops.app_type(deployment)):
            selector = {
                "labelSelector": "app.kubernetes.io/instance="
                + layout.application(project, app, component, environment)
            }
            replica_sets = kube_list(
                f"/apis/apps/v1/namespaces/{namespace}/replicasets",
                selector,
                cloud=cloud,
            )
            deployment_obj = kube_get(
                f"/apis/apps/v1/namespaces/{namespace}/deployments/"
                + layout.chart_fullname(project, app, component),
                cloud=cloud,
            )
            found += revisions_of(
                environment, component, replica_sets, deployment_obj
            )
    return found


def _next_outcome(
    row: ReleaseEvent, top: int, current: RollingRevision | None
) -> str | None:
    """Where a row moves to given what is seen now, None to leave it."""
    if row.outcome == "running":
        # An older revision's ReplicaSet may be gone: compare numbers only.
        if row.revision < top:
            return "superseded"
        if current and current.outcome != "running":
            return current.outcome
    # A failed rollout recovers when its pods come up late. Only the newest
    # revision tells: the older ones are always labelled deployed.
    elif row.outcome == "failed" and row.revision == top:
        if current and current.outcome == "deployed":
            return "deployed"
    return None


def sync_rolling(
    db: Session, deployment_id: int, revisions: list[RollingRevision]
) -> int:
    """
    Bring the rolling rows in line with *revisions*, return how many changed.

    Commits only when something changed.
    """
    rows = (
        db.query(ReleaseEvent)
        .filter(
            ReleaseEvent.deployment_id == deployment_id,
            ReleaseEvent.strategy == "rolling",
        )
        .all()
    )
    seen = {(r.environment, r.component, r.revision): r for r in revisions}
    newest: dict[tuple[str, str], int] = {}
    for r in revisions:
        key = (r.environment, r.component)
        newest[key] = max(newest.get(key, 0), r.revision)
    now = datetime.now(timezone.utc)
    known = set()
    changes = 0

    for row in rows:
        key = (row.environment, row.component, row.revision)
        known.add(key)
        top = newest.get(key[:2])
        outcome = top and _next_outcome(row, top, seen.get(key))
        if outcome:
            row.outcome = outcome
            row.finished_at = now
            changes += 1

    for key, r in seen.items():
        if key not in known:
            db.add(
                ReleaseEvent(
                    deployment_id=deployment_id,
                    environment=r.environment,
                    component=r.component,
                    revision=r.revision,
                    image=r.image,
                    strategy="rolling",
                    started_at=r.started_at,
                    finished_at=None if r.outcome == "running" else now,
                    outcome=r.outcome,
                    forced=False,
                    actors=[],
                )
            )
            changes += 1

    if changes:
        db.commit()
    return changes
