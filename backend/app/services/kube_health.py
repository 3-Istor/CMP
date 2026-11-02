"""
Health of a Kubernetes app, per component, read from its own cluster.

Pods give readiness and crash loops, offhours-guard's schedules say which
components sleep, VictoriaMetrics counts restarts, and the on-prem Argo CD
gives sync and health.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.services.environments import gitops, layout
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_get,
    kube_list,
)
from app.services.metrics_isolation import VMSELECT, cluster_filter
from app.services.monitoring_service import (
    AppHealthResponse,
    ArgoState,
    ComponentHealth,
)

APPLICATIONS = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"
SCHEDULES = "/apis/guard.3istor.com/v1alpha1/namespaces/{}/offhoursschedules"
# offhours-guard scales a schedule's targets to 0 in these states.
ASLEEP_STATES = {"SLEEPING", "MANUAL_SLEEP"}
DESIRED_ANNOTATIONS = (
    "deployment.kubernetes.io/desired-replicas",
    "rollout.argoproj.io/desired-replicas",
)
# Not ready for longer than this: missing replicas degrade, none ready is down.
GRACE = timedelta(minutes=5)
RESTARTS_DEGRADED = 3
# The app takes the status of its worst component.
SEVERITY = (
    "healthy",
    "asleep",
    "degraded",
    "down",
    "unknown",
    "not_connected",
)


def _ready_since(pod: dict[str, Any]) -> tuple[bool, datetime]:
    """Whether the pod is Ready and since when; created-at before any probe."""
    for condition in pod.get("status", {}).get("conditions") or []:
        if condition.get("type") == "Ready":
            return condition.get("status") == "True", datetime.fromisoformat(
                condition["lastTransitionTime"]
            )
    return False, datetime.fromisoformat(pod["metadata"]["creationTimestamp"])


def _crash_looping(pod: dict[str, Any]) -> bool:
    status = pod.get("status", {})
    return any(
        (s.get("state") or {}).get("waiting", {}).get("reason")
        == "CrashLoopBackOff"
        for s in (status.get("initContainerStatuses") or [])
        + (status.get("containerStatuses") or [])
    )


def _desired(replica_sets: list[dict[str, Any]]) -> int:
    """
    The CMP may not read Deployments on-prem, but the Deployment (or Rollout)
    controller writes its replica count on every ReplicaSet it runs.
    """
    desired = 0
    for replica_set in replica_sets:
        # An old revision at 0 keeps the count it had then.
        if not replica_set.get("spec", {}).get("replicas"):
            continue
        annotations = replica_set["metadata"].get("annotations") or {}
        for key in DESIRED_ANNOTATIONS:
            if key in annotations:
                desired = max(desired, int(annotations[key]))
    return desired


def component_health(
    name: str,
    pods: list[dict[str, Any]],
    replica_sets: list[dict[str, Any]],
    asleep: bool,
    restarts: dict[str, float] | None,
    argocd: ArgoState | None,
    now: datetime,
) -> ComponentHealth:
    live = [
        p
        for p in pods
        if not p["metadata"].get("deletionTimestamp")
        and p.get("status", {}).get("phase") not in ("Succeeded", "Failed")
    ]
    readiness = [_ready_since(p) for p in live]
    ready = sum(is_ready for is_ready, _ in readiness)
    desired = _desired(replica_sets)
    recent = (
        None
        if restarts is None
        else round(sum(restarts.get(p["metadata"]["name"], 0) for p in live))
    )
    # ponytail: a replica whose pod was never created (quota, admission) has
    # no date, so it never degrades; read the ReplicaSet's ReplicaFailure if needed.
    stale = any(
        not is_ready and now - since > GRACE for is_ready, since in readiness
    )
    if asleep:
        status = "asleep"
    elif ready == 0:
        # Without any pod, nothing is starting up: no grace.
        status = "down" if stale or not live else "degraded"
    elif (
        (ready < desired and stale)
        or any(_crash_looping(p) for p in live)
        or (recent or 0) >= RESTARTS_DEGRADED
    ):
        status = "degraded"
    else:
        status = "healthy"
    return ComponentHealth(
        name=name,
        status=status,
        ready_replicas=ready,
        desired_replicas=desired,
        restarts_15m=recent,
        argocd=argocd,
    )


def _restarts(namespace: str, cloud: TargetCloud) -> dict[str, float] | None:
    """Container restarts of the last 15 min per pod; None when unreadable."""
    try:
        response = httpx.get(
            f"{VMSELECT}/api/v1/query",
            params={
                "query": "sum by (pod) (increase("
                f'kube_pod_container_status_restarts_total{{namespace="{namespace}"}}'
                "[15m]))",
                "extra_filters[]": cluster_filter(cloud.value),
            },
            timeout=10.0,
        )
        response.raise_for_status()
        return {
            r["metric"]["pod"]: float(r["value"][1])
            for r in response.json()["data"]["result"]
        }
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _argocd(application: str) -> ArgoState | None:
    """Read from the on-prem Argo CD, which deploys to every cloud."""
    try:
        app = kube_get(f"{APPLICATIONS}/{application}")
    except KubeUnavailableError:
        return None
    if app is None:
        return None
    status = app.get("status") or {}
    return ArgoState(
        sync=(status.get("sync") or {}).get("status"),
        health=(status.get("health") or {}).get("status"),
    )


def app_health(
    deployment: Deployment, cloud: TargetCloud, now: datetime | None = None
) -> AppHealthResponse:
    """The prod environment's health; an unreadable cluster is a status, not an error."""
    now = now or datetime.now(timezone.utc)
    project, app = deployment.project_id, deployment.name
    namespace = layout.namespace(project, app, "prod")
    names = layout.components(gitops.app_type(deployment))
    releases = {c: layout.application(project, app, c, "prod") for c in names}
    argocd = {c: _argocd(releases[c]) for c in names}
    try:
        asleep = {
            ref.get("name")
            for schedule in kube_list(SCHEDULES.format(namespace), cloud=cloud)
            if (schedule.get("status") or {}).get("currentState")
            in ASLEEP_STATES
            for ref in schedule.get("spec", {}).get("targetRefs") or []
        }
        restarts = _restarts(namespace, cloud)
        components = []
        for c in names:
            selector = {
                "labelSelector": f"app.kubernetes.io/instance={releases[c]}"
            }
            components.append(
                component_health(
                    c,
                    kube_list(
                        f"/api/v1/namespaces/{namespace}/pods",
                        selector,
                        cloud=cloud,
                    ),
                    kube_list(
                        f"/apis/apps/v1/namespaces/{namespace}/replicasets",
                        selector,
                        cloud=cloud,
                    ),
                    layout.chart_fullname(project, app, c) in asleep,
                    restarts,
                    argocd[c],
                    now,
                )
            )
    except (ClusterNotConnectedError, KubeUnavailableError) as exc:
        status = (
            "not_connected"
            if isinstance(exc, ClusterNotConnectedError)
            else "unknown"
        )
        components = [
            ComponentHealth(
                name=c,
                status=status,
                ready_replicas=None,
                desired_replicas=None,
                restarts_15m=None,
                argocd=argocd[c],
            )
            for c in names
        ]
    return AppHealthResponse(
        deployment_name=deployment.name,
        status=max((c.status for c in components), key=SEVERITY.index),
        components=components,
    )
