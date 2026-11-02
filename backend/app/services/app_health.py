"""
Real health of a Kubernetes app, one state per component.

Read from the pods and ReplicaSets of the prod namespace: the CMP service
account cannot read Deployments on-prem, but every ReplicaSet carries the
desired replica count of its workload in an annotation.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.services import monitoring_service
from app.services.environments import gitops, layout
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_list,
)
from app.services.links import APPLICATIONS_PATH
from app.services.metrics_isolation import VMSELECT, cluster_filter

logger = logging.getLogger(__name__)

SLOW_ROLLOUT = timedelta(minutes=5)
RESTART_THRESHOLD = 3
SLEEPING_STATES = {"SLEEPING", "MANUAL_SLEEP"}
DESIRED_ANNOTATIONS = (
    "deployment.kubernetes.io/desired-replicas",
    "rollout.argoproj.io/desired-replicas",
)
SCHEDULES_PATH = (
    "/apis/guard.3istor.com/v1alpha1/namespaces/{}/offhoursschedules"
)
# Worst last: the app takes the highest.
SEVERITY = (
    "healthy",
    "asleep",
    "unknown",
    "not_connected",
    "degraded",
    "down",
)


@dataclass
class _Observed:
    pods: list[dict[str, Any]]
    replica_sets: list[dict[str, Any]]


def worst(states: list[str]) -> str:
    return max(states, key=SEVERITY.index) if states else "unknown"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_ready(pod: dict[str, Any]) -> bool:
    return any(
        c.get("type") == "Ready" and c.get("status") == "True"
        for c in pod.get("status", {}).get("conditions") or []
    )


def _is_crashlooping(pod: dict[str, Any]) -> bool:
    return any(
        (s.get("state", {}).get("waiting") or {}).get("reason")
        == "CrashLoopBackOff"
        for s in pod.get("status", {}).get("containerStatuses") or []
    )


def desired_replicas(replica_sets: list[dict[str, Any]]) -> int | None:
    """None when there is no ReplicaSet to read; 0 when the workload is scaled down."""
    if not replica_sets:
        return None
    desired = 0
    for rs in replica_sets:
        if not rs.get("spec", {}).get("replicas"):
            continue
        annotations = rs.get("metadata", {}).get("annotations") or {}
        declared = [
            int(annotations[key])
            for key in DESIRED_ANNOTATIONS
            if annotations.get(key, "").isdigit()
        ]
        desired = max(desired, *declared, rs["spec"]["replicas"])
    return desired


def _not_ready_since(
    pods: list[dict[str, Any]], replica_sets: list[dict[str, Any]]
) -> datetime | None:
    since = [
        _parse_time(c.get("lastTransitionTime"))
        for pod in pods
        if not _is_ready(pod)
        for c in pod.get("status", {}).get("conditions") or []
        if c.get("type") == "Ready"
    ]
    if not since:
        since = [
            _parse_time(rs.get("metadata", {}).get("creationTimestamp"))
            for rs in replica_sets
            if rs.get("spec", {}).get("replicas")
        ]
    known = [t for t in since if t]
    return min(known) if known else None


def _sleeping_targets(schedules: list[dict[str, Any]]) -> set[str]:
    return {
        ref["name"]
        for schedule in schedules
        if (schedule.get("status") or {}).get("currentState")
        in SLEEPING_STATES
        for ref in schedule.get("spec", {}).get("targetRefs") or []
    }


def classify(
    pods: list[dict[str, Any]],
    replica_sets: list[dict[str, Any]],
    asleep: bool,
    restarts: int | None,
    now: datetime,
) -> tuple[str, str | None, int, int]:
    """(state, reason, ready, desired) of one component."""
    live = [
        p for p in pods if not p.get("metadata", {}).get("deletionTimestamp")
    ]
    ready = sum(1 for p in live if _is_ready(p))
    desired = desired_replicas(replica_sets)
    if asleep:
        return "asleep", "sleeping", ready, 0
    if desired is None and not live:
        return "unknown", "no_workload", 0, 0
    desired = desired or 0
    crashlooping = any(_is_crashlooping(p) for p in live)
    if ready == 0:
        reason = "crashloop" if crashlooping else "no_ready_replica"
        if desired == 0:
            reason = "scaled_to_zero"
        return "down", reason, ready, desired
    if crashlooping:
        return "degraded", "crashloop", ready, desired
    if ready < desired:
        since = _not_ready_since(live, replica_sets)
        if since and now - since > SLOW_ROLLOUT:
            return "degraded", "rollout_slow", ready, desired
    if restarts is not None and restarts >= RESTART_THRESHOLD:
        return "degraded", "restarts", ready, desired
    return "healthy", None, ready, desired


def _restarts_by_pod(namespace: str, cloud: str) -> dict[str, int] | None:
    query = (
        "sum by (pod) (increase(kube_pod_container_status_restarts_total"
        f'{{namespace="{namespace}"}}[15m]))'
    )
    try:
        response = httpx.get(
            f"{VMSELECT}/api/v1/query",
            params={
                "query": query,
                "extra_filters[]": cluster_filter(cloud),
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return {
            r["metric"]["pod"]: round(float(r["value"][1]))
            for r in response.json()["data"]["result"]
            if "pod" in r["metric"]
        }
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Restarts of %s not readable: %s", namespace, exc)
        return None


def _argocd_applications(
    project: str, app: str
) -> dict[str, tuple[str | None, str | None]]:
    try:
        items = kube_list(
            APPLICATIONS_PATH,
            {
                "labelSelector": f"cnp.3istor.com/app={app},"
                f"cnp.3istor.com/project={project}"
            },
        )
    except KubeUnavailableError as exc:
        logger.warning("Argo CD Applications of %s/%s: %s", project, app, exc)
        return {}
    return {
        item["metadata"]["name"]: (
            (item.get("status", {}).get("sync") or {}).get("status"),
            (item.get("status", {}).get("health") or {}).get("status"),
        )
        for item in items
    }


def _observe(
    project: str, app: str, components: list[str], cloud: TargetCloud
) -> tuple[dict[str, _Observed], list[dict[str, Any]]]:
    namespace = layout.namespace(project, app, "prod")
    observed = {}
    for component in components:
        selector = {
            "labelSelector": "app.kubernetes.io/instance="
            + layout.application(project, app, component, "prod")
        }
        observed[component] = _Observed(
            pods=kube_list(
                f"/api/v1/namespaces/{namespace}/pods", selector, cloud=cloud
            ),
            replica_sets=kube_list(
                f"/apis/apps/v1/namespaces/{namespace}/replicasets",
                selector,
                cloud=cloud,
            ),
        )
    schedules = kube_list(SCHEDULES_PATH.format(namespace), cloud=cloud)
    return observed, schedules


def _read_app_health(
    deployment: Deployment, now: datetime
) -> monitoring_service.AppHealthResponse:
    project = deployment.project_id or ""
    app = deployment.name
    components = layout.components(gitops.app_type(deployment))
    cloud_name = gitops.target_cloud(deployment)
    cloud = TargetCloud(cloud_name)
    try:
        observed, schedules = _observe(project, app, components, cloud)
    except (ClusterNotConnectedError, KubeUnavailableError) as exc:
        logger.warning("Cluster of %s/%s not reachable: %s", project, app, exc)
        return monitoring_service.AppHealthResponse(
            deployment_name=app,
            status="not_connected",
            aws_frontend=None,
            openstack_backend=None,
            components=[
                monitoring_service.ComponentHealth(
                    name=c, state="not_connected", reason="cluster_unreachable"
                )
                for c in components
            ],
        )

    sleeping = _sleeping_targets(schedules)
    applications = _argocd_applications(project, app)
    restarts = _restarts_by_pod(
        layout.namespace(project, app, "prod"), cloud_name
    )
    results = []
    for component in components:
        data = observed[component]
        pod_names = {p["metadata"]["name"] for p in data.pods}
        recent = (
            None
            if restarts is None
            else sum(v for k, v in restarts.items() if k in pod_names)
        )
        state, reason, ready, desired = classify(
            data.pods,
            data.replica_sets,
            layout.chart_fullname(project, app, component) in sleeping,
            recent,
            now,
        )
        sync, health = applications.get(
            layout.application(project, app, component, "prod"), (None, None)
        )
        results.append(
            monitoring_service.ComponentHealth(
                name=component,
                state=state,
                ready=ready,
                desired=desired,
                restarts_15m=recent,
                argocd_sync=sync,
                argocd_health=health,
                reason=reason,
            )
        )
    return monitoring_service.AppHealthResponse(
        deployment_name=app,
        status=worst([c.state for c in results]),
        aws_frontend=None,
        openstack_backend=None,
        components=results,
    )


async def get_kubernetes_app_health(
    deployment: Deployment,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> monitoring_service.AppHealthResponse:
    return await asyncio.to_thread(_read_app_health, deployment, clock())
