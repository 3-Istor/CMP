"""Where a project's pods run, per cluster, read live (platform admins only)."""

import re

from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import TargetCloud
from app.services import project_registry
from app.services.clusters.state import PUBLIC_CLOUDS, fresh_state
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_list,
)

CLOUDS = (TargetCloud.ONPREM, *PUBLIC_CLOUDS)
PROJECT_LABEL = "cnp.3istor.com/project"
INSTANCE_TYPE = "node.kubernetes.io/instance-type"
CNPG_CLUSTERS = "/apis/postgresql.cnpg.io/v1/namespaces/{}/clusters"
OBC = "/apis/objectbucket.io/v1alpha1/namespaces/{}-backups/objectbucketclaims"


_UNITS = {
    "": 1, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
    "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
}  # fmt: skip


def quantity_bytes(value: str) -> float:
    """A Kubernetes memory quantity (6085816Ki, 100M, 1Gi, 1e9, 500m) in bytes."""
    match = re.fullmatch(r"([0-9.]+(?:[eE][0-9]+)?)([A-Za-z]*)", str(value))
    if not match or match.group(2) not in _UNITS:
        raise ValueError(f"unsupported quantity {value}")
    return float(match.group(1)) * _UNITS[match.group(2)]


def pod_status(pod: dict) -> str:
    if pod["metadata"].get("deletionTimestamp"):
        return "Terminating"
    status = pod.get("status") or {}
    phase = status.get("phase", "Pending")
    # Running with a container not ready yet is still starting.
    if phase == "Running" and not all(
        c.get("ready") for c in status.get("containerStatuses") or []
    ):
        return "Starting"
    return phase


def pod_kind(pod: dict) -> str:
    meta = pod["metadata"]
    labels = meta.get("labels") or {}
    name = meta["name"]
    if "cnpg.io/cluster" in labels:
        return "db"
    if labels.get("cnp.3istor.com/role") == "migration":
        return "job"
    if "-tunnel-" in name:
        return "tunnel"
    if name.startswith("gatus-") or "-gatus-" in name:
        return "monitor"
    # Staging apps carry a -stg suffix on the instance label.
    instance = (labels.get("app.kubernetes.io/instance") or "").removesuffix(
        "-stg"
    )
    if instance.endswith("-backend"):
        return "api"
    if instance.endswith(("-frontend", "-app")):
        return "web"
    return "system"


def database(cluster: dict) -> dict:
    """A CNPG Cluster as shown: generation is the -r<N> restore suffix."""
    name = cluster["metadata"]["name"]
    restored = re.search(r"-r(\d+)$", name)
    archiving = next(
        (
            c
            for c in (cluster.get("status") or {}).get("conditions") or []
            if c.get("type") == "ContinuousArchiving"
        ),
        None,
    )
    return {
        "name": name,
        "generation": int(restored.group(1)) if restored else 0,
        "last_archived_wal": (cluster.get("status") or {}).get(
            "lastArchivedWALTime"
        )
        or (archiving or {}).get("lastTransitionTime"),
        "archiving": archiving and archiving.get("status") == "True",
    }


def console_url(cloud: TargetCloud, project: str | None = None) -> str:
    s = settings
    if cloud == TargetCloud.AWS:
        return (
            f"https://{s.EKS_REGION}.console.aws.amazon.com/eks/clusters/"
            f"{s.EKS_CLUSTER}?region={s.EKS_REGION}&selectedTab=cluster-resources-tab"
        )
    if cloud == TargetCloud.GCP:
        return (
            "https://console.cloud.google.com/kubernetes/workload/overview"
            f"?project={s.GCP_PROJECT_ID}"
        )
    url = f"{s.ARGOCD_URL}/applications"
    return (
        f"{url}?labels={PROJECT_LABEL.replace('/', '%2F')}%3D{project}"
        if project
        else url
    )


def _state(cloud: TargetCloud) -> str:
    # The CMP runs on-prem: no Argo Application tracks that cluster.
    return "up" if cloud == TargetCloud.ONPREM else fresh_state(cloud)


def _cluster_pods(cloud: TargetCloud, project: str) -> tuple[list, list]:
    """Pods of the project's namespaces and the CNPG clusters among them."""
    namespaces = kube_list(
        "/api/v1/namespaces",
        {"labelSelector": f"{PROJECT_LABEL}={project}"},
        cloud=cloud,
    )
    pods, databases = [], []
    for ns in namespaces:
        name = ns["metadata"]["name"]
        pods += kube_list(f"/api/v1/namespaces/{name}/pods", cloud=cloud)
        databases += kube_list(CNPG_CLUSTERS.format(name), cloud=cloud)
    return pods, databases


def project_placement(project: str, current: TargetCloud) -> dict:
    clusters, db = [], None
    for cloud in CLOUDS:
        entry = {
            "cloud": cloud.value,
            "state": _state(cloud),
            "console_url": console_url(cloud, project),
            "nodes": [],
        }
        clusters.append(entry)
        if entry["state"] != "up":
            continue
        try:
            nodes = _nodes(cloud)
            pods, dbs = _cluster_pods(cloud, project)
        except (ClusterNotConnectedError, KubeUnavailableError):
            entry["state"] = "unreachable"
            continue
        by_node = {
            n["metadata"]["name"]: {
                "name": n["metadata"]["name"],
                "instance_type": (n["metadata"].get("labels") or {}).get(
                    INSTANCE_TYPE
                ),
                "pods": [],
            }
            for n in nodes
        }
        for pod in pods:
            node_name = pod.get("spec", {}).get("nodeName")
            if node_name:
                node = by_node.setdefault(
                    node_name,
                    {"name": node_name, "instance_type": None, "pods": []},
                )
                node["pods"].append(
                    {
                        "name": pod["metadata"]["name"],
                        "namespace": pod["metadata"]["namespace"],
                        "status": pod_status(pod),
                        "kind": pod_kind(pod),
                    }
                )
        # Only nodes that host the project's pods are worth drawing.
        entry["nodes"] = [n for n in by_node.values() if n["pods"]]
        if dbs and (db is None or cloud == current):
            db = database(dbs[0])
    return {
        "current_cloud": current.value,
        "clusters": clusters,
        "database": db,
        "backups": _backups(project),
    }


def _nodes(cloud: TargetCloud) -> list[dict]:
    """Nodes; none when the CMP may not list them (pods are still placed)."""
    try:
        return kube_list("/api/v1/nodes", cloud=cloud)
    except KubeUnavailableError as exc:
        if exc.status == 403:
            return []
        raise


def _backups(project: str) -> list[dict]:
    try:
        claims = kube_list(OBC.format(project))
    except (ClusterNotConnectedError, KubeUnavailableError):
        return []
    return [
        {
            "claim": c["metadata"]["name"],
            "bucket": (c.get("spec") or {}).get("bucketName"),
            "uid": c["metadata"].get("uid"),
        }
        for c in claims
    ]


def _memory_requested_pct(cloud: TargetCloud) -> float | None:
    try:
        nodes = kube_list("/api/v1/nodes", cloud=cloud)
        pods = kube_list("/api/v1/pods", cloud=cloud)
        allocatable = sum(
            quantity_bytes(n["status"]["allocatable"]["memory"]) for n in nodes
        )
        requested = sum(
            quantity_bytes(c["resources"]["requests"]["memory"])
            for p in pods
            # A finished pod holds no memory.
            if (p.get("status") or {}).get("phase")
            not in ("Succeeded", "Failed")
            for c in p.get("spec", {}).get("containers", [])
            if (c.get("resources") or {}).get("requests", {}).get("memory")
        )
    except (
        ClusterNotConnectedError,
        KubeUnavailableError,
        KeyError,
        ValueError,
    ):
        return None
    return round(100 * requested / allocatable, 1) if allocatable else None


def visible_app_counts(db: Session) -> dict[str, int]:
    """
    Apps per project as the project page counts them: the CMP's apps, not
    the registry's entries, which also hold the agents and apps in flight.
    """
    return dict(
        db.query(Deployment.project_id, func.count(Deployment.id))
        .filter(
            Deployment.project_id.isnot(None),
            Deployment.status != DeploymentStatus.DELETED,
        )
        .group_by(Deployment.project_id)
        .all()
    )


async def platform_placement(app_counts: dict[str, int]) -> dict:
    records = await project_registry.list_records()
    columns = {c: [] for c in CLOUDS}
    for record in records:
        spec, move = record.get("spec") or {}, (
            record.get("status") or {}
        ).get("move")
        name = record["metadata"]["name"]
        apps = app_counts.get(name, 0)
        if move:
            placed = [
                (
                    move["source"],
                    {"moving_to": move["target"], "moving_from": None},
                ),
                (
                    move["target"],
                    {"moving_to": None, "moving_from": move["source"]},
                ),
            ]
        else:
            placed = [
                (
                    spec.get("targetCloud"),
                    {"moving_to": None, "moving_from": None},
                )
            ]
        for cloud, moving in placed:
            if cloud in {c.value for c in CLOUDS}:
                columns[TargetCloud(cloud)].append(
                    {"name": name, "apps": apps, **moving}
                )
    clusters = []
    for cloud in CLOUDS:
        state = await run_in_threadpool(_state, cloud)
        nodes, pct = 0, None
        if state == "up":
            try:
                nodes = len(await run_in_threadpool(_nodes, cloud))
                pct = await run_in_threadpool(_memory_requested_pct, cloud)
            except (ClusterNotConnectedError, KubeUnavailableError):
                state = "unreachable"
        clusters.append(
            {
                "cloud": cloud.value,
                "state": state,
                "console_url": console_url(cloud),
                "nodes": nodes,
                "memory_requested_pct": pct,
                "projects": columns[cloud],
            }
        )
    return {"clusters": clusters}
