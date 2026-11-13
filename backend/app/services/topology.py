"""
The project topology (CNP-242): its apps and who calls whom.

Nodes come from the registry record, the deployments rows and the import
rows; health and sync from the Argo CD Applications, sleep from the
OffhoursSchedules. Declared edges are the records' ``dependsOn``; observed
and denied edges come from the Hubble flow metrics in VictoriaMetrics, in a
query built here from the project's own namespaces: nothing the browser
sends reaches PromQL.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.deployment import Deployment, DeploymentStatus
from app.models.imports import ImportService, ImportSource
from app.models.project import Project, TargetCloud
from app.services import topology_cache
from app.services.app_health import SCHEDULES_PATH, SLEEPING_STATES
from app.services.environments import gitops, layout
from app.services.imports.app_links import reconcile_rows
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
    kube_list,
)
from app.services.links import APPLICATIONS_PATH
from app.services.metrics_isolation import VMSELECT, cluster_filter
from app.services.project_registry import read_record_cached

logger = logging.getLogger(__name__)

FLOW_METRIC = "hubble_flows_processed_total"
# Workload labels are often empty and destinations outside the cluster have
# no namespace: apps are told apart by namespace only.
FLOW_LABELS = "source_namespace, destination_namespace, verdict, type"
RATE_WINDOW = "5m"
COUNT_WINDOW = "1h"
IMPORTED_TYPES = ("service", "backing")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Argo CD health, best first: a node shows the worst of its components.
HEALTH_ORDER = (
    "healthy",
    "progressing",
    "suspended",
    "unknown",
    "missing",
    "degraded",
)
SYNC_ORDER = ("synced", "unknown", "outofsync")
METRIC_PRESENT_TTL_SECONDS = 300.0
KUBE_CONCURRENCY = 4

# {cloud: (expires at, metric present)}
_metric_present: dict[str, tuple[float, bool]] = {}


class MetricsUnavailableError(Exception):
    pass


def _worst(values: list[str], order: tuple[str, ...]) -> str | None:
    known = [v for v in values if v in order]
    return max(known, key=order.index) if known else None


def _short_digest(images: list[str]) -> str | None:
    for image in images:
        if "@sha256:" in image:
            return "sha256:" + image.rsplit("@sha256:", 1)[1]
    return None


def edge_state(declared: bool, forwarded: float, dropped: float) -> str:
    """The four states of cnp-docs 09/02-architecture."""
    if forwarded <= 0 < dropped:
        return "denied"
    if declared:
        return "declared_observed" if forwarded > 0 else "declared_only"
    return "observed_only"


# ── Observed flows ──────────────────────────────────────────────────────────


def flow_query(namespaces: list[str], function: str, window: str) -> str:
    """
    PromQL over the project's flows. Namespaces are matched exactly, so a
    project whose name prefixes another's never reads the other's flows.
    """
    for namespace in namespaces:
        if not _NAME.match(namespace):
            raise ValueError(f"invalid namespace {namespace!r}")
    matcher = "|".join(sorted(namespaces))
    selector = (
        f'{FLOW_METRIC}{{source_namespace=~"{matcher}",'
        f'destination_namespace=~"{matcher}"}}'
    )
    return f"sum by ({FLOW_LABELS}) ({function}({selector}[{window}]))"


async def _vm_query(
    client: httpx.AsyncClient, query: str, cloud: str
) -> list[dict[str, Any]]:
    try:
        response = await client.get(
            "/api/v1/query",
            params={"query": query, "extra_filters[]": cluster_filter(cloud)},
        )
        response.raise_for_status()
        return response.json()["data"]["result"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise MetricsUnavailableError(str(exc)) from exc


Flows = dict[tuple[str, str], dict[str, float]]


def _by_pair(
    results: list[dict[str, Any]], namespace_app: dict[str, str]
) -> dict[tuple[str, str, str], dict[str, float]]:
    """{(from app, to app, type): {verdict: value}}, flows inside an app left out."""
    pairs: dict[tuple[str, str, str], dict[str, float]] = {}
    for item in results:
        labels = item.get("metric") or {}
        source = namespace_app.get(labels.get("source_namespace", ""))
        target = namespace_app.get(labels.get("destination_namespace", ""))
        if source is None or target is None or source == target:
            continue
        try:
            value = float(item["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        verdict = str(labels.get("verdict", "")).upper()
        kind = str(labels.get("type", "")) or "Trace"
        bucket = pairs.setdefault((source, target, kind), {})
        bucket[verdict] = bucket.get(verdict, 0.0) + value
    return pairs


def initiated(
    typed: dict[tuple[str, str, str], dict[str, float]],
    declared: set[tuple[str, str]],
) -> Flows:
    """
    One direction per conversation: the flow metric counts replies too.

    Heuristic, per unordered pair of apps:
    - when PolicyVerdict flows exist for the pair, only they count: Cilium
      emits them for the side that opens the connection;
    - otherwise Trace (and Drop) flows count, and forwarded traffic seen in
      both directions is kept only in the declared direction(s), or else in
      the busier one (both on a tie, as nothing tells them apart).
    Drops are never replies and always count in their own direction.
    """
    unordered: dict[frozenset, list[tuple[str, str, str]]] = {}
    for key in typed:
        unordered.setdefault(frozenset(key[:2]), []).append(key)
    flows: Flows = {}
    for keys in unordered.values():
        verdict_keys = [k for k in keys if k[2] == "PolicyVerdict"]
        chosen = verdict_keys or [k for k in keys if k[2] != "PolicyVerdict"]
        directed: Flows = {}
        for source, target, kind in chosen:
            bucket = directed.setdefault((source, target), {})
            for verdict, value in typed[(source, target, kind)].items():
                bucket[verdict] = bucket.get(verdict, 0.0) + value
        forwarded = {
            pair: v.get("FORWARDED", 0.0)
            for pair, v in directed.items()
            if v.get("FORWARDED", 0.0) > 0
        }
        if not verdict_keys and len(forwarded) == 2:
            keep = {pair for pair in forwarded if pair in declared}
            if not keep:
                top = max(forwarded.values())
                keep = {p for p, v in forwarded.items() if v == top}
            for pair in forwarded:
                if pair not in keep:
                    directed[pair]["FORWARDED"] = 0.0
        flows.update(directed)
    return flows


async def _flow_metric_present(client: httpx.AsyncClient, cloud: str) -> bool:
    """Whether Hubble metrics reach VictoriaMetrics at all, for 5 minutes."""
    cached = _metric_present.get(cloud)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    present = bool(await _vm_query(client, f"count({FLOW_METRIC})", cloud))
    _metric_present[cloud] = (
        time.monotonic() + METRIC_PRESENT_TTL_SECONDS,
        present,
    )
    return present


async def observed_flows(
    project: str,
    apps: list[str],
    cloud: str,
    declared: set[tuple[str, str]] | None = None,
) -> tuple[bool, dict[tuple[str, str], dict[str, float]]]:
    """
    ``(available, {(from, to): {rps, count, forwarded, dropped}})``.

    The Hubble series carry no ``namespace`` label, so the per-project vmauth
    users cannot read them: the CMP asks vmselect itself, with a query naming
    the project's namespaces only. Not available when the metric is missing
    from VictoriaMetrics or VictoriaMetrics cannot be read.

    Prod namespaces only: the map shows the prod links, and staging traffic
    must not mark one of them observed.
    """
    namespace_app = {
        layout.namespace(project, app, "prod"): app for app in apps
    }
    if not namespace_app:
        return False, {}
    namespaces = list(namespace_app)
    try:
        async with httpx.AsyncClient(base_url=VMSELECT, timeout=10) as client:
            rates = await _vm_query(
                client, flow_query(namespaces, "rate", RATE_WINDOW), cloud
            )
            counts = await _vm_query(
                client,
                flow_query(namespaces, "increase", COUNT_WINDOW),
                cloud,
            )
            if not rates and not counts:
                if not await _flow_metric_present(client, cloud):
                    return False, {}
    except MetricsUnavailableError as exc:
        logger.warning("Flows of %s not readable: %s", project, exc)
        return False, {}
    rate_pairs = initiated(_by_pair(rates, namespace_app), declared or set())
    count_pairs = initiated(_by_pair(counts, namespace_app), declared or set())
    flows = {}
    for pair in set(rate_pairs) | set(count_pairs):
        rate = rate_pairs.get(pair, {})
        count = count_pairs.get(pair, {})
        forwarded = count.get("FORWARDED", 0.0) + rate.get("FORWARDED", 0.0)
        dropped = count.get("DROPPED", 0.0) + rate.get("DROPPED", 0.0)
        if forwarded <= 0 and dropped <= 0:
            continue
        flows[pair] = {
            "rps": round(rate.get("FORWARDED", 0.0), 3),
            "count": round(
                count.get("FORWARDED", 0.0) + count.get("DROPPED", 0.0)
            ),
            "forwarded": forwarded,
            "dropped": dropped,
        }
    return True, flows


# ── Nodes ───────────────────────────────────────────────────────────────────


def _kind(record_app: dict | None, row: Deployment | None) -> str:
    if record_app and record_app.get("type") in (
        "static",
        "fullstack",
        "service",
        "backing",
    ):
        return record_app["type"]
    if row is not None:
        if row.template_id == "import-backing":
            return "backing"
        if row.template_id == "import-service":
            return "service"
        return "fullstack" if gitops.app_type(row) == "fullstack" else "static"
    return "static"


def _argo_by_name(project: str) -> dict[str, dict]:
    """The project's Argo CD Applications by name; empty when unreadable."""
    try:
        items = kube_list(
            APPLICATIONS_PATH,
            {"labelSelector": f"cnp.3istor.com/project={project}"},
        )
    except KubeUnavailableError as exc:
        logger.warning("Argo CD Applications of %s: %s", project, exc)
        return {}
    return {item["metadata"]["name"]: item for item in items}


def _asleep(namespace: str, cloud: TargetCloud) -> bool | None:
    try:
        schedules = kube_list(SCHEDULES_PATH.format(namespace), cloud=cloud)
    except (KubeUnavailableError, ClusterNotConnectedError):
        return None
    return any(
        (s.get("status") or {}).get("currentState") in SLEEPING_STATES
        for s in schedules
    )


async def _cluster_state(
    project: str, apps: dict[str, str], cloud: TargetCloud
) -> tuple[dict[str, dict], dict[str, bool | None]]:
    """(Argo Applications by name, asleep by app); kube calls, threaded."""
    limit = asyncio.Semaphore(KUBE_CONCURRENCY)

    async def asleep(app: str) -> tuple[str, bool | None]:
        async with limit:
            namespace = layout.namespace(project, app, "prod")
            return app, await asyncio.to_thread(_asleep, namespace, cloud)

    applications, *states = await asyncio.gather(
        asyncio.to_thread(_argo_by_name, project),
        *(asleep(app) for app in apps),
    )
    return applications, dict(states)


def _node(
    project: str,
    name: str,
    kind: str,
    record_app: dict | None,
    row: Deployment | None,
    imported: ImportService | None,
    applications: dict[str, dict],
    asleep: bool | None,
) -> dict:
    components = (
        layout.components("fullstack") if kind == "fullstack" else ["app"]
    )
    health, sync, images = [], [], []
    for component in components:
        status = (
            applications.get(
                layout.application(project, name, component, "prod")
            )
            or {}
        ).get("status") or {}
        health.append(str((status.get("health") or {}).get("status", "")))
        sync.append(str((status.get("sync") or {}).get("status", "")))
        images += (status.get("summary") or {}).get("images") or []
    record_app = record_app or {}
    hostname = (record_app.get("hostnames") or {}).get("prod")
    if kind in IMPORTED_TYPES:
        public = bool(record_app.get("public"))
    else:
        public = bool(hostname)
    spec = imported.spec if imported is not None else {}
    return {
        "id": name,
        "app": name,
        "kind": kind,
        "components": components,
        "public": public,
        "hostname": hostname if public else None,
        "health": _worst([h.lower() for h in health], HEALTH_ORDER),
        "sync": _worst([s.lower() for s in sync], SYNC_ORDER),
        "asleep": asleep,
        "digest": (imported.last_digest if imported else None)
        or _short_digest(images),
        "imported": imported is not None,
        "backing_kind": spec.get("backing_kind") or None,
        "ports": [
            {"name": p["name"], "port": p["port"]}
            for p in spec.get("ports") or []
        ],
        "linkable": kind in IMPORTED_TYPES and imported is not None,
        "deployment_id": row.id if row is not None else None,
        "in_registry": bool(record_app),
    }


def _declared(
    apps: dict[str, dict],
) -> dict[tuple[str, str, str], dict]:
    edges = {}
    for name, app in apps.items():
        for link in app.get("dependsOn") or []:
            if not isinstance(link, dict) or link.get("app") not in apps:
                continue
            alias = link.get("alias") or link["app"]
            edges[(name, link["app"], alias)] = {
                "port": link.get("port"),
                "alias": alias,
            }
    return edges


async def cached_build(db: Session, project: str) -> dict:
    """:func:`build`, kept for ``topology_cache.TTL_SECONDS``."""
    cached = topology_cache.get(project)
    if cached is not None:
        return cached
    generation = topology_cache.generation(project)
    topology = await build(db, project)
    topology_cache.put(project, topology, generation)
    return topology


async def build(db: Session, project: str) -> dict:
    record = await read_record_cached(project)
    record_apps = {
        app["name"]: app
        for app in ((record or ({}, ""))[0].get("spec") or {}).get("apps")
        or []
        if isinstance(app, dict) and _NAME.match(str(app.get("name", "")))
    }
    rows = {
        row.name: row
        for row in db.query(Deployment)
        .filter(
            Deployment.project_id == project,
            Deployment.status != DeploymentStatus.DELETED,
        )
        .all()
        if _NAME.match(row.name)
    }
    imported = {
        row.app_name: row
        for row in db.query(ImportService)
        .join(ImportSource)
        .filter(ImportSource.project_name == project)
        .all()
    }
    project_row = db.query(Project).filter_by(project_name=project).first()
    cloud = (
        project_row.target_cloud
        if project_row and project_row.target_cloud
        else TargetCloud.ONPREM
    )
    names = sorted(set(record_apps) | set(rows))
    kinds = {
        name: _kind(record_apps.get(name), rows.get(name)) for name in names
    }
    reconcile_rows(db, project, record_apps)
    applications, asleep = await _cluster_state(project, kinds, cloud)
    nodes = [
        _node(
            project,
            name,
            kinds[name],
            record_apps.get(name),
            rows.get(name),
            imported.get(name),
            applications,
            asleep.get(name),
        )
        for name in names
    ]

    declared = _declared({n: record_apps[n] for n in record_apps})
    available, flows = await observed_flows(
        project,
        names,
        TargetCloud(cloud).value,
        {(source, target) for source, target, _ in declared},
    )
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    edges = []
    seen_pairs = set()
    for (source, target, _alias), link in sorted(declared.items()):
        flow = flows.get((source, target))
        seen_pairs.add((source, target))
        edges.append(_edge(source, target, link, flow, now))
    for (source, target), flow in sorted(flows.items()):
        if (source, target) not in seen_pairs:
            edges.append(_edge(source, target, None, flow, now))
    return {
        "project": project,
        "nodes": nodes,
        "edges": edges,
        "observed_available": available,
        "generated_at": now,
    }


def _edge(
    source: str, target: str, link: dict | None, flow: dict | None, now: str
) -> dict:
    forwarded = flow["forwarded"] if flow else 0.0
    dropped = flow["dropped"] if flow else 0.0
    alias = link["alias"] if link else None
    observed = None
    if flow:
        observed = {
            "rps": flow["rps"],
            "count": flow["count"],
            "last_seen": now if flow["rps"] > 0 else None,
            "verdict": "forwarded" if forwarded > 0 else "dropped",
        }
    return {
        "id": f"{source}->{target}" + (f":{alias}" if alias else ""),
        "from": source,
        "to": target,
        "port": link["port"] if link else None,
        "alias": alias,
        "declared": link is not None,
        "observed": observed,
        "state": edge_state(link is not None, forwarded, dropped),
    }
