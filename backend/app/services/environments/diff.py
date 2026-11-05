"""What differs between an app's staging and prod: what each really runs."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.schemas.release import _Camel
from app.services import app_health
from app.services.environments import gitops, layout, observe
from app.services.kube_client import kube_list


class DiffField(BaseModel):
    key: str
    staging: str | None
    prod: str | None
    status: Literal["same", "differs", "unknown"]


class EnvironmentDiff(_Camel):
    staging_available: bool
    staging_asleep: bool
    fields: list[DiffField]


@dataclass
class Runtime:
    image_tag: str | None = None
    ready_replicas: str | None = None
    cpu_request: str | None = None
    memory_request: str | None = None

    def fields(self) -> dict[str, str | None]:
        return {
            "imageTag": self.image_tag,
            "readyReplicas": self.ready_replicas,
            "cpuRequest": self.cpu_request,
            "memoryRequest": self.memory_request,
        }


def _requests(pods: list[dict]) -> tuple[str | None, str | None]:
    for pod in pods:
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        for container in pod.get("spec", {}).get("containers") or []:
            if container.get("name") == observe.CONTAINER:
                requests = (container.get("resources") or {}).get(
                    "requests"
                ) or {}
                return requests.get("cpu"), requests.get("memory")
    return None, None


def runtime_of(pods: list[dict], replica_sets: list[dict]) -> Runtime:
    state = observe.component_state(pods)
    desired = app_health.desired_replicas(replica_sets)
    cpu, memory = _requests(pods)
    wanted = state.pods if desired is None else desired
    return Runtime(
        image_tag=state.version,
        ready_replicas=f"{state.ready}/{wanted}",
        cpu_request=cpu,
        memory_request=memory,
    )


def read_runtime(
    deployment: Deployment, environment: str
) -> dict[str, Runtime]:
    """
    Raises:
        KubeUnavailableError: The cluster cannot be read.
        ClusterNotConnectedError: The project's public cloud is not up.
    """
    project, app = deployment.project_id or "", deployment.name
    cloud = TargetCloud(gitops.target_cloud(deployment))
    namespace = layout.namespace(project, app, environment)
    found = {}
    for component in layout.components(gitops.app_type(deployment)):
        selector = {
            "labelSelector": "app.kubernetes.io/instance="
            + layout.application(project, app, component, environment)
        }
        pods = kube_list(
            f"/api/v1/namespaces/{namespace}/pods", selector, cloud=cloud
        )
        replica_sets = kube_list(
            f"/apis/apps/v1/namespaces/{namespace}/replicasets",
            selector,
            cloud=cloud,
        )
        found[component] = runtime_of(pods, replica_sets)
    return found


def is_asleep(runtime: dict[str, Runtime]) -> bool:
    # Off-hours scheduler scaled it to zero; one live component is not asleep.
    return bool(runtime) and all(
        r.ready_replicas == "0/0" for r in runtime.values()
    )


def _status(
    staging: str | None, prod: str | None
) -> Literal["same", "differs", "unknown"]:
    if staging is None or prod is None:
        return "unknown"
    return "same" if staging == prod else "differs"


def fields_diff(
    staging: dict[str, Runtime], prod: dict[str, Runtime]
) -> list[DiffField]:
    single = len(prod) == 1
    rows = []
    for component, right in prod.items():
        runtime = staging.get(component, Runtime())
        # A component created after a wake keeps sleeping while others run.
        sleeping = runtime.ready_replicas == "0/0"
        left = runtime.fields()
        for name, b in right.fields().items():
            a = left[name]
            rows.append(
                DiffField(
                    key=name if single else f"{component}.{name}",
                    staging=a,
                    prod=b,
                    status="unknown" if sleeping else _status(a, b),
                )
            )
    return rows


def compute(
    staging_available: bool,
    staging_runtime: dict[str, Runtime],
    prod_runtime: dict[str, Runtime],
) -> EnvironmentDiff:
    if not staging_available:
        return EnvironmentDiff(
            staging_available=False, staging_asleep=False, fields=[]
        )
    return EnvironmentDiff(
        staging_available=True,
        staging_asleep=is_asleep(staging_runtime),
        fields=fields_diff(staging_runtime, prod_runtime),
    )
