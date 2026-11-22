"""
Pre-pull of the source images on every schedulable target node, during prepare.

One short-lived Pod per (image, node) in the project's app namespace on the
target, so the cut-over finds the images already in the node caches.
"""

import hashlib
import logging

from app.models.project import TargetCloud
from app.models.project_move import ProjectMove
from app.services.kube_client import (
    KubeUnavailableError,
    kube_create,
    kube_delete,
    kube_get,
    kube_list,
)
from app.services.moves.steps import (
    CNPG,
    LABEL,
    NAMESPACES,
    WORKLOADS,
    _app_namespaces,
)

logger = logging.getLogger(__name__)

PREPULL = "cnp.3istor.com/prepull"
PULLING = ("ContainerCreating", "ErrImagePull", "ImagePullBackOff")
PULL_ERRORS = ("ErrImagePull", "ImagePullBackOff")


def _images(project: str, source: TargetCloud) -> dict[str, dict[str, list]]:
    """{namespace: {image: imagePullSecrets}} of what the source runs."""
    found: dict[str, dict[str, list]] = {}
    for ns in _app_namespaces(project, source):
        images = found.setdefault(ns, {})
        for collection in WORKLOADS:
            for item in kube_list(collection.format(ns), cloud=source):
                # A Rollout with workloadRef has no template: the Deployment has.
                template = (item["spec"].get("template") or {}).get(
                    "spec"
                ) or {}
                secrets = template.get("imagePullSecrets") or []
                for c in (template.get("containers") or []) + (
                    template.get("initContainers") or []
                ):
                    images.setdefault(c["image"], secrets)
        for cluster in kube_list(f"{CNPG.format(ns)}/clusters", cloud=source):
            image = (cluster.get("status") or {}).get("image") or (
                cluster.get("spec") or {}
            ).get("imageName")
            if image:
                secrets = cluster["spec"].get("imagePullSecrets") or []
                images.setdefault(image, secrets)
    return found


def _nodes(target: TargetCloud) -> list[str]:
    """Ready nodes a pod without tolerations can land on."""
    names = []
    for node in kube_list("/api/v1/nodes", cloud=target):
        spec, status = node.get("spec") or {}, node.get("status") or {}
        ready = any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in status.get("conditions") or []
        )
        tainted = any(
            t.get("effect") in ("NoSchedule", "NoExecute")
            for t in spec.get("taints") or []
        )
        if ready and not tainted and not spec.get("unschedulable"):
            names.append(node["metadata"]["name"])
    return names


def _pod(move: ProjectMove, ns: str, image: str, node: str, secrets: list):
    digest = hashlib.sha1(f"{image}{node}".encode()).hexdigest()[:10]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"prepull-{move.id}-{digest}",
            "namespace": ns,
            "labels": {PREPULL: str(move.id), LABEL: move.project},
        },
        "spec": {
            "nodeName": node,
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "imagePullSecrets": secrets,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65534,
                "runAsGroup": 65534,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "pull",
                    "image": image,
                    "command": ["sh", "-c", "true"],
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "16Mi"},
                        "limits": {"cpu": "50m", "memory": "32Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }


def _state(pod: dict) -> tuple[str, str]:
    """("pulled" | "pulling" | "failed", reason)."""
    status = pod.get("status") or {}
    statuses = status.get("containerStatuses") or []
    waiting = (
        ((statuses[0].get("state") or {}).get("waiting") or {})
        if statuses
        else None
    )
    if waiting is None:
        # Rejected by the kubelet before any container status (e.g. out of resources).
        if status.get("phase") == "Failed":
            return "failed", status.get("reason") or "Failed"
        return "pulling", ""
    reason = waiting.get("reason") or ""
    if reason in PULL_ERRORS:
        return "failed", reason
    return ("pulling", reason) if reason in PULLING else ("pulled", reason)


def pending(move: ProjectMove) -> list[str]:
    """Create the missing pre-pull Pods; the waits for images still pulling."""
    source, target = TargetCloud(move.source), TargetCloud(move.target)
    nodes = _nodes(target)
    waits = []
    for ns, images in _images(move.project, source).items():
        if not images:
            continue
        if not kube_get(f"{NAMESPACES}/{ns}", cloud=target):
            waits.append(f"Waiting for namespace {ns} on {move.target}")
            continue
        path = f"{NAMESPACES}/{ns}/pods"
        existing = {
            p["metadata"]["name"]: p
            for p in kube_list(
                path, {"labelSelector": f"{PREPULL}={move.id}"}, cloud=target
            )
        }
        for image, secrets in images.items():
            for node in nodes:
                pod = _pod(move, ns, image, node, secrets)
                name = pod["metadata"]["name"]
                if name not in existing:
                    try:
                        kube_create(path, pod, cloud=target)
                    except KubeUnavailableError as exc:
                        # 403: pods quota full or admission refusal; retried next tick.
                        if exc.status != 403:
                            raise
                        logger.warning("Pre-pull Pod refused: %s", exc)
                    waits.append(f"Pulling {image} on {node}")
                    continue
                state, reason = _state(existing[name])
                if state == "pulling":
                    waits.append(f"Pulling {image} on {node}")
                elif state == "failed":
                    logger.warning(
                        "Pre-pull of %s on %s failed: %s", image, node, reason
                    )
    return waits


def cleanup(move: ProjectMove) -> None:
    """Delete this move's pre-pull Pods on the target."""
    target = TargetCloud(move.target)
    for ns in _app_namespaces(move.project, target):
        path = f"{NAMESPACES}/{ns}/pods"
        for pod in kube_list(
            path, {"labelSelector": f"{PREPULL}={move.id}"}, cloud=target
        ):
            if (pod["metadata"].get("labels") or {}).get(PREPULL) == str(
                move.id
            ):
                kube_delete(f"{path}/{pod['metadata']['name']}", cloud=target)
