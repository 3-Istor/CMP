"""
What differs between an app's staging and prod: what each really runs, read
from the pods, and the values each one is deployed with, read from the repo.

Never carries a value found under a secret-looking key.
"""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.schemas.release import _Camel
from app.services import app_health
from app.services.environments import gitops, layout, observe
from app.services.kube_client import kube_list

SENSITIVE_KEY = re.compile(r"secret|password|passwd|token|api_?key", re.I)
IMAGE_ROOT = "image"


class DiffField(BaseModel):
    key: str
    staging: str | None
    prod: str | None
    differs: bool


class DiffValue(BaseModel):
    path: str
    staging: Any = None
    prod: Any = None


class EnvironmentDiff(_Camel):
    staging_available: bool
    fields: list[DiffField]
    values: list[DiffValue]


@dataclass
class Runtime:
    image_tag: str | None = None
    image_digest: str | None = None
    ready_replicas: str | None = None
    cpu_request: str | None = None
    memory_request: str | None = None

    def fields(self) -> dict[str, str | None]:
        return {
            "imageTag": self.image_tag,
            "imageDigest": self.image_digest,
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
        image_digest=state.digest,
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


def merged(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result


def flatten(values: Any, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if not isinstance(values, dict):
        return flat
    for key, value in values.items():
        key = str(key)
        if SENSITIVE_KEY.search(key) or (not prefix and key == IMAGE_ROOT):
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            flat.update(flatten(value, path))
        else:
            flat[path] = value
    return flat


def values_diff(
    staging: dict[str, dict[str, Any]], prod: dict[str, dict[str, Any]]
) -> list[DiffValue]:
    single = len(prod) == 1
    rows = []
    for component in prod:
        left = flatten(staging.get(component, {}))
        right = flatten(prod[component])
        for path in sorted(left.keys() | right.keys()):
            if left.get(path) == right.get(path):
                continue
            rows.append(
                DiffValue(
                    path=path if single else f"{component}.{path}",
                    staging=left.get(path),
                    prod=right.get(path),
                )
            )
    return rows


def fields_diff(
    staging: dict[str, Runtime], prod: dict[str, Runtime]
) -> list[DiffField]:
    single = len(prod) == 1
    rows = []
    for component, right in prod.items():
        left = staging.get(component, Runtime())
        left_fields, right_fields = left.fields(), right.fields()
        for name, b in right_fields.items():
            a = left_fields[name]
            rows.append(
                DiffField(
                    key=name if single else f"{component}.{name}",
                    staging=a,
                    prod=b,
                    differs=a is not None and b is not None and a != b,
                )
            )
    return rows


def compute(
    staging_available: bool,
    staging_runtime: dict[str, Runtime],
    prod_runtime: dict[str, Runtime],
    staging_values: dict[str, dict[str, Any]],
    prod_values: dict[str, dict[str, Any]],
) -> EnvironmentDiff:
    if not staging_available:
        return EnvironmentDiff(staging_available=False, fields=[], values=[])
    return EnvironmentDiff(
        staging_available=True,
        fields=fields_diff(staging_runtime, prod_runtime),
        values=values_diff(staging_values, prod_values),
    )


async def read_values(
    deployment: Deployment, environment: str, ref: str = "main"
) -> dict[str, dict[str, Any]]:
    """component -> shared file merged with the environment's own file."""
    token = await gitops.token_for(deployment)
    repo = gitops.repo_of(deployment)
    kind = gitops.app_type(deployment)
    result = {}
    for component in layout.components(kind):
        base = await gitops.read_values(
            token, repo, layout.base_values_file(kind, component), ref
        )
        own = await gitops.read_values(
            token, repo, layout.env_values_file(environment, component), ref
        )
        result[component] = merged(base or {}, own or {})
    return result
