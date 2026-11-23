"""Whether the target cluster has room for a project: its pod requests against
the free allocatable CPU and memory of the target nodes."""

import logging
from datetime import datetime

from app.models.project import TargetCloud
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_list,
)
from app.services.moves import footprint
from app.services.placement import quantity_bytes

logger = logging.getLogger(__name__)

GIB = 2**30
MIB = 2**20
# infra-templates values.yaml `resources.requests`: what an app component asks
# when its values file sets nothing.
APP_CPU = 0.1
APP_MEMORY = 128 * MIB
# LimitRange cnp-project-defaults (cnp-project-base templates/namespace.yaml):
# defaultRequest of a container that declares none. The CNPG Cluster template
# (infra-templates cnpg-cluster.yaml) sets no resources, so every database
# container takes it: postgres, plus the barman-cloud plugin sidecar when
# db.backup.enabled (cnpg-cluster.yaml `plugins`).
LIMIT_CPU = 0.05
LIMIT_MEMORY = 128 * MIB
# infra-templates values.yaml `db.pooler.resources.requests`.
POOLER_CPU = 0.01
POOLER_MEMORY = 64 * MIB
# cnp-project-base requests of the system namespace, summed: Gatus 10m/64Mi
# (templates/gatus.yaml) + its sidecar 5m/64Mi (same file), cloudflared
# `tunnel.resources` 10m/32Mi x `tunnel.replicas` 1, `offhours-guard.resources`
# 10m/48Mi, Envoy proxy `gateway.resources` 25m/192Mi (values.yaml).
SYSTEM_CPU = 0.010 + 0.005 + 0.010 + 0.010 + 0.025
SYSTEM_MEMORY = (64 + 64 + 32 + 48 + 192) * MIB


def _asked(resources, cpu: float, memory: float) -> tuple[float, float]:
    asked = (resources or {}).get("requests") or {}
    return (
        quantity_bytes(asked["cpu"]) if "cpu" in asked else cpu,
        quantity_bytes(asked["memory"]) if "memory" in asked else memory,
    )


def component_need(values: dict) -> tuple[float, float]:
    """(CPU, memory bytes) of one app component's values file: its replicas
    (the autoscaling floor when on) plus its database and pooler pods."""
    scaled = values.get("autoscaling") or {}
    replicas = (
        scaled.get("minReplicas", 1)
        if scaled.get("enabled")
        else values.get("replicaCount", 1)
    )
    # `resources: null` deletes the chart default: the LimitRange applies.
    if "resources" in values and values["resources"] is None:
        cpu, memory = LIMIT_CPU, LIMIT_MEMORY
    else:
        cpu, memory = _asked(values.get("resources"), APP_CPU, APP_MEMORY)
    cpu, memory = cpu * replicas, memory * replicas
    db = values.get("db") or {}
    if db.get("enabled"):
        instances = db.get("instances", 1)
        containers = 2 if (db.get("backup") or {}).get("enabled") else 1
        cpu += LIMIT_CPU * containers * instances
        memory += LIMIT_MEMORY * containers * instances
        pooler = db.get("pooler") or {}
        if pooler.get("enabled"):
            c, m = _asked(pooler.get("resources"), POOLER_CPU, POOLER_MEMORY)
            cpu += c * pooler.get("instances", 1)
            memory += m * pooler.get("instances", 1)
    return cpu, memory


def estimate(apps: list[list[dict]]) -> tuple[float, float]:
    """The project's need from its apps' component values, plus the system
    part: what a move asks when the source cannot be read."""
    needs = [component_need(v) for components in apps for v in components]
    return (
        SYSTEM_CPU + sum(c for c, _ in needs),
        SYSTEM_MEMORY + sum(m for _, m in needs),
    )


def _requests(pods: list[dict]) -> tuple[float, float]:
    cpu, memory, _ = footprint.pod_requests(pods)
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


def check(
    needed: tuple[float, float],
    cloud: TargetCloud,
    basis: str = "live",
    measured_at: datetime | None = None,
) -> dict:
    """What *cloud* can take of *needed*; *basis* is live (read on the source),
    cache (last values seen) or estimate (declared values). An unknown room
    fits: the move is not blocked on a guess."""
    room = free(cloud)
    fits = room is None or (needed[0] <= room[0] and needed[1] <= room[1])
    return {
        "target": cloud.value,
        "need_cpu": needed[0],
        "need_memory_bytes": needed[1],
        "free_cpu": None if room is None else max(room[0], 0),
        "free_memory_bytes": None if room is None else max(room[1], 0),
        "basis": basis,
        "measured_at": measured_at.isoformat() if measured_at else None,
        "estimated": basis != "live",
        "fits": fits,
    }


_TAG = {"live": "", "cache": " (last seen)", "estimate": " (estimated)"}


def message(result: dict) -> str:
    return (
        f"Not enough room on {result['target'].upper()}"
        f"{_TAG[result['basis']]}: needs "
        f"{result['need_cpu']:.1f} CPU / "
        f"{result['need_memory_bytes'] / GIB:.1f} GiB, free "
        f"{result['free_cpu']:.1f} / {result['free_memory_bytes'] / GIB:.1f}"
    )


def refusal(result: dict) -> str | None:
    """Only a need read live on the source refuses."""
    return (
        None
        if result["fits"] or result["basis"] != "live"
        else message(result)
    )


def warning(result: dict) -> str | None:
    return (
        message(result)
        if not result["fits"] and result["basis"] != "live"
        else None
    )
