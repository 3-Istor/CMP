"""Whether the target cluster has room for a project: its pod requests against
the free allocatable CPU and memory of the target nodes."""

import logging

from app.models.project import TargetCloud
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_list,
)
from app.services.placement import quantity_bytes

logger = logging.getLogger(__name__)

GIB = 2**30
# ResourceQuota of every app namespace (cnp-project-base, namespace.yaml): the
# worst case a project can request when its source cannot be read.
QUOTA_CPU = 1.0
QUOTA_MEMORY = 2 * GIB


def _requests(pods: list[dict]) -> tuple[float, float]:
    cpu = memory = 0.0
    for pod in pods:
        # A finished pod holds nothing.
        if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        for c in (pod.get("spec") or {}).get("containers") or []:
            asked = (c.get("resources") or {}).get("requests") or {}
            cpu += quantity_bytes(asked.get("cpu", "0"))
            memory += quantity_bytes(asked.get("memory", "0"))
    return cpu, memory


def project_requests(
    namespaces: list[str], cloud: TargetCloud
) -> tuple[float, float]:
    """(CPU, memory bytes) the project's pods request on *cloud*."""
    pods = [
        pod
        for ns in namespaces
        for pod in kube_list(f"/api/v1/namespaces/{ns}/pods", cloud=cloud)
    ]
    return _requests(pods)


def quota_requests(namespaces: int) -> tuple[float, float]:
    return namespaces * QUOTA_CPU, namespaces * QUOTA_MEMORY


def free(cloud: TargetCloud) -> tuple[float, float] | None:
    """(CPU, memory bytes) left on *cloud*; None when it cannot be told."""
    try:
        nodes = kube_list("/api/v1/nodes", cloud=cloud)
        pods = kube_list("/api/v1/pods", cloud=cloud)
        cpu = sum(
            quantity_bytes(n["status"]["allocatable"]["cpu"]) for n in nodes
        )
        memory = sum(
            quantity_bytes(n["status"]["allocatable"]["memory"]) for n in nodes
        )
        used_cpu, used_memory = _requests(pods)
    except (
        ClusterNotConnectedError,
        KubeUnavailableError,
        KeyError,
        ValueError,
    ) as exc:
        # No node list (403) or no answer: the move is not blocked on a guess.
        logger.warning("capacity of %s unknown: %s", cloud.value, exc)
        return None
    if not nodes:
        return None
    return cpu - used_cpu, memory - used_memory


def refusal(needed: tuple[float, float], cloud: TargetCloud) -> str | None:
    """Why *cloud* cannot take *needed*; None when it can (or is unknown)."""
    room = free(cloud)
    if room is None or (needed[0] <= room[0] and needed[1] <= room[1]):
        return None
    return (
        f"Not enough room on {cloud.value.upper()}: needs "
        f"{needed[0]:.1f} CPU / {needed[1] / GIB:.1f} GiB, free "
        f"{max(room[0], 0):.1f} / {max(room[1], 0) / GIB:.1f}"
    )
