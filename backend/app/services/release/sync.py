"""
Bring an app's release history in line with its Rollout.

Shared by the Release endpoints and the background recorder, so a release is
recorded even when nobody has the Release tab open.
"""

from sqlalchemy.orm import Session

from app.models.deployment import Deployment, ProviderType
from app.models.release import ReleaseEvent
from app.schemas.release import ReleaseView
from app.services.clusters.state import project_cloud
from app.services.release import history
from app.services.release.source import ReleaseSource, RolloutSnapshot
from app.services.release.view import (
    REVISION_ANNOTATION,
    build_view,
    disabled_view,
)


def target(deployment: Deployment) -> tuple[str, str] | None:
    """Namespace and app name of a Kubernetes app, None for any other."""
    if deployment.provider_type != ProviderType.KUBERNETES:
        return None
    namespace = deployment.k8s_namespace or (
        f"{deployment.project_id}-{deployment.name}"
        if deployment.project_id
        else None
    )
    return (namespace, deployment.name) if namespace else None


def revision(snapshot: RolloutSnapshot) -> int | None:
    raw = (
        snapshot.rollout["metadata"]
        .get("annotations", {})
        .get(REVISION_ANNOTATION)
    )
    return int(raw) if raw else None


def compute(
    db: Session, deployment: Deployment, source: ReleaseSource
) -> tuple[ReleaseView, RolloutSnapshot | None, ReleaseEvent | None]:
    """Read the Rollout, map it, and bring the history in line.

    Raises KubeUnavailableError when the cluster cannot be reached,
    ClusterNotConnectedError when it is a public cloud's that is not up.
    """
    app_target = target(deployment)
    if app_target is None:
        return disabled_view(), None, None
    snapshot = source.fetch(
        *app_target, project_cloud(db, deployment.project_id)
    )
    if snapshot is None:
        return disabled_view(), None, None

    before = history.latest_event(db, deployment.id)
    view = build_view(
        snapshot, hint=history.hint_from(before, revision(snapshot))
    )
    event = history.sync_event(db, deployment.id, view)
    if event is not None and event is not before:
        # A new attempt: the previous row's skips and force no longer apply.
        view = build_view(
            snapshot, hint=history.hint_from(event, revision(snapshot))
        )
    return view, snapshot, event
