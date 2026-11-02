"""
Manual sleep, wake and restart of an app's production workloads.

Sleep and wake go through offhours-guard, the same way its own buttons do:
the OffhoursSchedule status carries MANUAL_SLEEP or MANUAL_WAKE and the
operator scales the targets. Restart sets the Argo Rollout ``restartAt`` field
when the component has a Rollout, otherwise the pod template annotation that
``kubectl rollout restart`` uses.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.services.environments import layout, observe
from app.services.kube_client import (
    KubeUnavailableError,
    kube_get,
    kube_list,
    kube_merge_patch,
)

SCHEDULES = "/apis/guard.3istor.com/v1alpha1/namespaces/{ns}/offhoursschedules"
ROLLOUT = "/apis/argoproj.io/v1alpha1/namespaces/{ns}/rollouts/{name}"
WORKLOAD = "/apis/apps/v1/namespaces/{ns}/deployments/{name}"
RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"

MANUAL_SLEEP = "MANUAL_SLEEP"
MANUAL_WAKE = "MANUAL_WAKE"


class NoScheduleError(Exception):
    """The app has no OffhoursSchedule, so offhours-guard cannot move it."""


class AsleepError(Exception):
    """Restarting an app scaled to zero would do nothing."""


@dataclass
class PowerState:
    asleep: bool
    scheduled: bool


def prod_namespace(deployment: Deployment) -> str:
    return layout.namespace(deployment.project_id, deployment.name, "prod")


def read(deployment: Deployment, cloud: TargetCloud) -> PowerState:
    namespace = prod_namespace(deployment)
    schedules = kube_list(SCHEDULES.format(ns=namespace), cloud=cloud)
    state = observe.of(deployment, "prod")
    return PowerState(asleep=state.asleep, scheduled=bool(schedules))


def set_state(deployment: Deployment, target: str, cloud: TargetCloud) -> int:
    """Return how many schedules were asked to move."""
    namespace = prod_namespace(deployment)
    schedules = kube_list(SCHEDULES.format(ns=namespace), cloud=cloud)
    if not schedules:
        raise NoScheduleError(namespace)
    for schedule in schedules:
        name = schedule["metadata"]["name"]
        kube_merge_patch(
            f"{SCHEDULES.format(ns=namespace)}/{name}/status",
            {"status": {"currentState": target}},
            cloud=cloud,
        )
    return len(schedules)


def restart(
    deployment: Deployment, app_type: str, cloud: TargetCloud
) -> list[str]:
    """Return the workloads that were restarted."""
    if observe.of(deployment, "prod").asleep:
        raise AsleepError(deployment.name)
    namespace = prod_namespace(deployment)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    restarted = []
    for component in layout.components(app_type):
        name = layout.chart_fullname(
            deployment.project_id, deployment.name, component
        )
        if kube_get(ROLLOUT.format(ns=namespace, name=name), cloud=cloud):
            kube_merge_patch(
                ROLLOUT.format(ns=namespace, name=name),
                {"spec": {"restartAt": stamp}},
                cloud=cloud,
            )
        else:
            kube_merge_patch(
                WORKLOAD.format(ns=namespace, name=name),
                {
                    "spec": {
                        "template": {
                            "metadata": {"annotations": {RESTARTED_AT: stamp}}
                        }
                    }
                },
                cloud=cloud,
            )
        restarted.append(name)
    return restarted


def is_forbidden(exc: KubeUnavailableError) -> bool:
    return "HTTP 403" in str(exc)
