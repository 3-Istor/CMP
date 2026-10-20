"""
What an environment really runs, read from its pods.

A promotion copies these digests to prod, never a tag: a tag can be pushed
again, a running pod's imageID cannot change.
"""

from dataclasses import dataclass, field

from app.models.deployment import Deployment
from app.services.environments import gitops, layout, registry
from app.services.kube_client import kube_get, kube_list, kube_merge_patch

CONTAINER = "cnp-generic-app"


@dataclass
class ComponentState:
    pods: int = 0
    ready: int = 0
    # repository@sha256:..., set when every pod runs the same image.
    digest: str | None = None
    # The tag the pods were started from (1.4.0-rc.2).
    version: str | None = None


@dataclass
class EnvironmentState:
    namespace: str
    components: dict[str, ComponentState] = field(default_factory=dict)

    @property
    def converged(self) -> bool:
        """
        Every component is up, runs a single image, and they all run the same
        version: the image updater moves a fullstack app one component per
        commit, and the state in between is not a release.
        """
        return (
            bool(self.components)
            and all(
                c.pods > 0 and c.ready == c.pods and c.digest
                for c in self.components.values()
            )
            and len({c.version for c in self.components.values()}) == 1
        )

    @property
    def asleep(self) -> bool:
        """
        Any component scaled to zero: each has its own offhours schedule, and
        one created after a wake keeps sleeping while the others run.
        """
        return any(c.pods == 0 for c in self.components.values())

    @property
    def digests(self) -> dict[str, str]:
        return {
            name: c.digest for name, c in self.components.items() if c.digest
        }

    @property
    def version(self) -> str | None:
        versions = {c.version for c in self.components.values() if c.version}
        return versions.pop() if len(versions) == 1 else None


def _split(image: str) -> tuple[str, str | None]:
    """(repository, tag) of ghcr.io/o/app:1.4.0@sha256:..., tag None if absent."""
    name = image.split("@", 1)[0]
    head, _, last = name.rpartition("/")
    if ":" in last:
        last, tag = last.split(":", 1)
        return f"{head}/{last}" if head else last, tag
    return name, None


def _digest(image_id: str) -> str | None:
    """
    repository@sha256 from a container status, None before the pull.

    imageID, not image: containerd sometimes reports the image config ID
    (sha256:...) as status.image.
    """
    if "@sha256:" not in image_id:
        return None
    repository, digest = image_id.rsplit("@", 1)
    return f"{_split(repository)[0]}@{digest}"


def _tag(image: str) -> str | None:
    return _split(image)[1]


def component_state(pods: list[dict]) -> ComponentState:
    state = ComponentState()
    digests: set[str | None] = set()
    versions: set[str | None] = set()
    for pod in pods:
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        statuses = [
            s
            for s in pod.get("status", {}).get("containerStatuses") or []
            if s.get("name") == CONTAINER
        ]
        if not statuses:
            continue
        status = statuses[0]
        spec_image = next(
            (
                c.get("image", "")
                for c in pod.get("spec", {}).get("containers") or []
                if c.get("name") == CONTAINER
            ),
            "",
        )
        state.pods += 1
        if status.get("ready"):
            state.ready += 1
        digests.add(_digest(status.get("imageID", "")))
        versions.add(_tag(spec_image))
    if len(digests) == 1:
        state.digest = digests.pop()
    if len(versions) == 1:
        state.version = versions.pop()
    return state


def observe(
    project: str, app: str, app_type: str, environment: str
) -> EnvironmentState:
    """
    Raises:
        KubeUnavailableError: The cluster cannot be read.
    """
    namespace = layout.namespace(project, app, environment)
    state = EnvironmentState(namespace=namespace)
    for component in layout.components(app_type):
        release = layout.application(project, app, component, environment)
        pods = kube_list(
            f"/api/v1/namespaces/{namespace}/pods",
            {"labelSelector": f"app.kubernetes.io/instance={release}"},
        )
        state.components[component] = component_state(pods)
    return state


def wake(namespace: str) -> int:
    """
    Ask offhours-guard to wake every schedule of a namespace until its next
    planned sleep, the same thing its own Wake button does.

    Returns how many schedules were woken.
    """
    schedules = kube_list(
        f"/apis/guard.3istor.com/v1alpha1/namespaces/{namespace}/offhoursschedules"
    )
    for schedule in schedules:
        name = schedule["metadata"]["name"]
        kube_merge_patch(
            f"/apis/guard.3istor.com/v1alpha1/namespaces/{namespace}/offhoursschedules/{name}/status",
            {"status": {"currentState": "MANUAL_WAKE"}},
        )
    return len(schedules)


def of(deployment: Deployment, environment: str) -> EnvironmentState:
    """
    What an app's environment runs, wherever it runs.

    On-prem, from the pods. On a public cloud the CMP has no credentials
    (D-02: only Argo CD holds them), so from the Applications' status, with
    the tag resolved to a digest at GHCR.

    Raises:
        KubeUnavailableError: The cluster cannot be read.
    """
    kind = gitops.app_type(deployment)
    if gitops.target_cloud(deployment) == "onprem":
        return observe(
            deployment.project_id, deployment.name, kind, environment
        )
    return observe_through_argocd(
        deployment.project_id, deployment.name, kind, environment
    )


def _image_of_component(
    images: list[str], app: str, component: str
) -> str | None:
    suffix = (
        f"/{app.lower()}"
        if component == "app"
        else f"/{app.lower()}/{component}"
    )
    found = [image for image in images if _split(image)[0].endswith(suffix)]
    return found[0] if len(found) == 1 else None


def observe_through_argocd(
    project: str, app: str, app_type: str, environment: str
) -> EnvironmentState:
    state = EnvironmentState(
        namespace=layout.namespace(project, app, environment)
    )
    for component in layout.components(app_type):
        name = layout.application(project, app, component, environment)
        application = (
            kube_get(
                f"/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/{name}"
            )
            or {}
        )
        status = application.get("status") or {}
        settled = (
            (status.get("sync") or {}).get("status") == "Synced"
            and (status.get("health") or {}).get("status") == "Healthy"
            and (status.get("operationState") or {}).get("phase") != "Running"
        )
        image = _image_of_component(
            (status.get("summary") or {}).get("images") or [], app, component
        )
        component_state = ComponentState(pods=1, ready=1 if settled else 0)
        if image:
            component_state.version = _tag(image)
            if "@sha256:" in image:
                component_state.digest = (
                    f"{_split(image)[0]}@{image.rsplit('@', 1)[1]}"
                )
            elif component_state.version:
                try:
                    component_state.digest = registry.resolve(
                        _split(image)[0], component_state.version
                    )
                except registry.RegistryLookupError:
                    component_state.digest = None
        state.components[component] = component_state
    return state
