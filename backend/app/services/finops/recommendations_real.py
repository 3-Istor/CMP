"""
Recommendations from measured usage in VictoriaMetrics, never from the
simulation: CPU p95 and memory p99 over 14 days against what the namespace
requests, and namespaces that received no traffic for 7 days.

Each cluster is read on its own (``cluster`` label) and priced at its rates:
the on-prem TCO, or the cloud node's list price (``inputs.cluster_rates``).
The on-prem cluster runs all the time; a cloud cluster only for demos, so its
monthly saving is scaled by the share of the window it had data.
"""

from __future__ import annotations

import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.services import metrics_isolation
from app.services.environments.observe import CONTAINER
from app.services.finops.engine import AppSpec
from app.services.finops.ingest import fx, inputs
from app.services.finops.recommendations import (
    SLEEP_HINT,
    Recommendation,
    k8s_resources,
)

logger = logging.getLogger(__name__)

WINDOW = "14d"
FULL_HISTORY_DAYS = 14
INACTIVITY_WINDOW = "7d"
INACTIVITY_MAX_BYTES = 1_000_000
HOURS_PER_MONTH = 730
CPU_HEADROOM = 1.3
RAM_HEADROOM = 1.2
TARGET_LOAD = 0.70
OVERSIZED_RATIO = 0.5
CACHE_TTL_SECONDS = 300
_NAMESPACE = re.compile(r"^[a-z0-9-]+$")

CLOUD_LABELS = {"aws": "AWS", "gcp": "GCP"}

_cache: dict[tuple[tuple[str, ...], str], tuple[float, dict]] = {}


def _instant(query: str, cluster: str) -> dict[str, float]:
    response = httpx.get(
        f"{metrics_isolation.VMSELECT}/api/v1/query",
        params={
            "query": query,
            "extra_filters[]": metrics_isolation.cluster_filter(cluster),
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return {
        r["metric"].get("namespace", ""): float(r["value"][1])
        for r in response.json()["data"]["result"]
    }


def _fetch(
    namespaces: tuple[str, ...], cluster: str
) -> dict[str, dict[str, float]]:
    cached = _cache.get((namespaces, cluster))
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    ns = f'namespace=~"{"|".join(namespaces)}"'
    pods = f'{ns},container!="",container!="POD"'
    requests = "kube_pod_container_resource_requests"
    limits = "kube_pod_container_resource_limits"
    # The namespace also runs databases, tunnels and migration jobs: only the
    # app container is rightsized, per pod.
    app = f'{ns},container="{CONTAINER}"'
    queries = {
        "cpu_request": (
            f'sum by (namespace) ({requests}{{{ns},resource="cpu"}})'
        ),
        "ram_request": (
            f'sum by (namespace) ({requests}{{{ns},resource="memory"}})'
        ),
        "app_cpu_request": (
            f'avg by (namespace) ({requests}{{{app},resource="cpu"}})'
        ),
        "app_ram_request": (
            f'avg by (namespace) ({requests}{{{app},resource="memory"}})'
        ),
        "app_cpu_limit": (
            f'avg by (namespace) ({limits}{{{app},resource="cpu"}})'
        ),
        "app_ram_limit": (
            f'avg by (namespace) ({limits}{{{app},resource="memory"}})'
        ),
        "app_cpu_p95": (
            "quantile_over_time(0.95, max by (namespace) "
            f"(rate(container_cpu_usage_seconds_total{{{app}}}[5m]))"
            f"[{WINDOW}:30m])"
        ),
        "app_ram_p99": (
            "quantile_over_time(0.99, max by (namespace) "
            f"(container_memory_working_set_bytes{{{app}}})"
            f"[{WINDOW}:30m])"
        ),
        "app_ram_peak": (
            "max by (namespace) (max_over_time("
            f"container_memory_working_set_bytes{{{app}}}[{WINDOW}]))"
        ),
        "replicas": (
            f"sum by (namespace) (kube_deployment_spec_replicas{{{ns}}})"
        ),
        "deployments": (
            f"count by (namespace) (kube_deployment_spec_replicas{{{ns}}})"
        ),
        "cpu_p95": (
            "quantile_over_time(0.95, sum by (namespace) "
            f"(rate(container_cpu_usage_seconds_total{{{pods}}}[5m]))"
            f"[{WINDOW}:30m])"
        ),
        "ram_p99": (
            "quantile_over_time(0.99, sum by (namespace) "
            f"(container_memory_working_set_bytes{{{pods}}})[{WINDOW}:30m])"
        ),
        "history_days": (
            "count_over_time(sum by (namespace) "
            f"(container_memory_working_set_bytes{{{pods}}})[{WINDOW}:1h]) / 24"
        ),
        "network_bytes": (
            "sum by (namespace) (increase("
            f"container_network_receive_bytes_total{{{ns}}}"
            f"[{INACTIVITY_WINDOW}]))"
        ),
    }
    # Sequential, these ~15 queries took seconds per page load.
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        results = pool.map(lambda q: _instant(q, cluster), queries.values())
        data = dict(zip(queries, results))
    _cache[(namespaces, cluster)] = (time.monotonic(), data)
    return data


def measured_usage(namespace: str, cluster: str = "onprem") -> dict:
    """
    What one namespace requests against what it used over WINDOW: CPU cores
    (p95) and memory GiB (p99), the same measures the recommendations use.

    Raises:
        httpx.HTTPError: VictoriaMetrics could not be read.
    """
    if not _NAMESPACE.match(namespace):
        raise ValueError(f"invalid namespace {namespace!r}")
    data = _fetch((namespace,), cluster)
    return {
        "cpu_request_cores": data["cpu_request"].get(namespace),
        "cpu_p95_cores": data["cpu_p95"].get(namespace),
        "ram_request_gib": _gib(data["ram_request"].get(namespace)),
        "ram_p99_gib": _gib(data["ram_p99"].get(namespace)),
        "history_days": data["history_days"].get(namespace, 0.0),
    }


def _gib(value: float | None) -> float | None:
    return None if value is None else value / 2**30


def _confidence(base: int, history_days: float) -> int:
    covered = min(1.0, history_days / FULL_HISTORY_DAYS)
    return int(max(10, base * (0.4 + 0.6 * covered)))


def _base_confidence(ratio: float) -> int:
    return int(max(60, min(98, 98 - ratio / OVERSIZED_RATIO * 38)))


def _usd_per_eur() -> float | None:
    try:
        with SessionLocal() as db:
            return fx.usd_per_eur(db, date.today())
    except SQLAlchemyError:
        return None


@dataclass(frozen=True)
class _Pricing:
    rates: inputs.NodeRates
    uptime: float

    def monthly_eur(self, cpu_cores: float, ram_gib: float) -> float:
        hourly = self.rates.hourly_eur(cpu_cores, ram_gib)
        return hourly * HOURS_PER_MONTH * self.uptime


def recommendations_for_apps(
    apps: list[AppSpec], ref: date | None = None
) -> list[Recommendation]:
    del ref
    by_namespace = {
        a.namespace: a
        for a in apps
        if a.namespace and _NAMESPACE.match(a.namespace)
    }
    if not by_namespace:
        return []
    try:
        rates = inputs.cluster_rates(_usd_per_eur())
    except OSError as exc:
        logger.warning("FinOps recommendations unpriced: %s", exc)
        return []
    recs: list[Recommendation] = []
    for cluster in inputs.CLUSTERS:
        if cluster not in rates:
            continue
        try:
            data = _fetch(tuple(sorted(by_namespace)), cluster)
            for namespace, app in by_namespace.items():
                recs.extend(
                    _for_namespace(
                        app, namespace, data, cluster, rates[cluster]
                    )
                )
        except (httpx.HTTPError, KeyError, ValueError, OSError) as exc:
            logger.warning(
                "FinOps recommendations unavailable for %s: %s", cluster, exc
            )
    recs.sort(key=lambda r: r.monthly_saving_eur, reverse=True)
    return recs


def _for_namespace(
    app: AppSpec,
    namespace: str,
    data: dict[str, dict[str, float]],
    cluster: str,
    rates: inputs.NodeRates,
) -> list[Recommendation]:
    cpu_req = data["cpu_request"].get(namespace, 0.0)
    ram_req = data["ram_request"].get(namespace, 0.0) / 2**30
    if cpu_req <= 0 and ram_req <= 0:
        return []
    history = data["history_days"].get(namespace, 0.0)
    pricing = _Pricing(
        rates,
        (
            1.0
            if cluster == "onprem"
            else min(1.0, history / FULL_HISTORY_DAYS)
        ),
    )
    recs = _advice(app, namespace, data, pricing, cpu_req, ram_req, history)
    if cluster == "onprem":
        return recs
    # On-prem ids stay as they were: dismissals are stored against them.
    label = CLOUD_LABELS.get(cluster, cluster)
    for rec in recs:
        rec.id = f"{rec.id}:{cluster}"
        rec.title = f"{rec.title} ({label})"
    return recs


def _advice(
    app: AppSpec,
    namespace: str,
    data: dict[str, dict[str, float]],
    pricing: _Pricing,
    cpu_req: float,
    ram_req: float,
    history: float,
) -> list[Recommendation]:
    common = {
        "app_id": app.id,
        "app_name": app.name,
        "project_id": app.project_id,
    }

    # One Deployment means one values file the patch can safely land in.
    single_deployment = data["deployments"].get(namespace, 0) == 1
    network = data["network_bytes"].get(namespace)
    if network is not None and network < INACTIVITY_MAX_BYTES and history >= 7:
        return [
            Recommendation(
                id=f"{app.id}:inactivity",
                rec_type="inactivity",
                title="Hibernate (inactive)",
                justification=(
                    "No significant inbound network traffic for 7 days. "
                    + SLEEP_HINT
                ),
                text_key="inactivity",
                current={"cpu_cores": round(cpu_req, 2), "status": "running"},
                recommended={"replicas": 0, "status": "hibernated"},
                monthly_saving_eur=round(
                    pricing.monthly_eur(cpu_req, ram_req), 2
                ),
                confidence=_confidence(80, history),
                effort="low",
                **common,
            )
        ]

    cpu_p95 = data["cpu_p95"].get(namespace)
    if cpu_p95 is None or cpu_req <= 0:
        return []

    replicas = int(data["replicas"].get(namespace, 0))
    cpu_ratio = cpu_p95 / cpu_req

    if single_deployment and replicas > 1 and cpu_ratio < OVERSIZED_RATIO:
        per_replica_cpu = cpu_req / replicas
        target = max(1, math.ceil(cpu_p95 / (per_replica_cpu * TARGET_LOAD)))
        if target < replicas:
            freed = replicas - target
            return [
                Recommendation(
                    id=f"{app.id}:replicas",
                    rec_type="replicas",
                    title="Reduce the replica count",
                    justification=(
                        f"CPU p95 over {WINDOW} is {cpu_ratio * 100:.0f}% "
                        f"of the requests (< 50%)."
                    ),
                    text_key="replicas",
                    text_params={
                        "window": WINDOW,
                        "pct": round(cpu_ratio * 100),
                    },
                    current={"replicas": replicas},
                    recommended={"replicas": target},
                    monthly_saving_eur=round(
                        pricing.monthly_eur(
                            per_replica_cpu * freed,
                            ram_req / replicas * freed,
                        ),
                        2,
                    ),
                    confidence=_confidence(
                        _base_confidence(cpu_ratio), history
                    ),
                    effort="low",
                    patch={"replicaCount": target},
                    **common,
                )
            ]

    pod_cpu = data["app_cpu_request"].get(namespace, 0.0)
    pod_ram = data["app_ram_request"].get(namespace, 0.0) / 2**30
    pod_cpu_p95 = data["app_cpu_p95"].get(namespace)
    pod_ram_p99 = data["app_ram_p99"].get(namespace)
    if pod_cpu <= 0 or pod_ram <= 0 or None in (pod_cpu_p95, pod_ram_p99):
        return []
    pod_ram_p99 /= 2**30
    cpu_ratio = pod_cpu_p95 / pod_cpu
    ram_ratio = pod_ram_p99 / pod_ram
    if cpu_ratio >= OVERSIZED_RATIO and ram_ratio >= OVERSIZED_RATIO:
        return []
    new_cpu = pod_cpu
    new_ram = pod_ram
    if cpu_ratio < OVERSIZED_RATIO:
        new_cpu = max(0.05, round(pod_cpu_p95 * CPU_HEADROOM, 2))
    if ram_ratio < OVERSIZED_RATIO:
        new_ram = max(0.05, pod_ram_p99 * RAM_HEADROOM)
    pods = max(1, replicas)
    saving = round(
        pricing.monthly_eur(
            max(0.0, pod_cpu - new_cpu) * pods,
            max(0.0, pod_ram - new_ram) * pods,
        ),
        2,
    )
    if saving <= 0:
        return []
    patch = None
    # A shorter window would size the memory limit from a partial peak.
    if single_deployment and history >= FULL_HISTORY_DAYS:
        patch = {
            "resources": _resources(
                namespace, data, pod_cpu, pod_ram, new_cpu, new_ram
            )
        }
    return [
        Recommendation(
            id=f"{app.id}:rightsizing",
            rec_type="rightsizing",
            title="Adjust the CPU / RAM requests",
            justification=(
                f"Over {WINDOW}, CPU p95 is at {cpu_ratio * 100:.0f}% and "
                f"memory p99 at {ram_ratio * 100:.0f}% of the requests."
            ),
            text_key="rightsizing",
            text_params={
                "window": WINDOW,
                "cpu": round(cpu_ratio * 100),
                "ram": round(ram_ratio * 100),
            },
            current={
                "cpu_cores": round(pod_cpu, 2),
                "ram_gb": round(pod_ram, 2),
            },
            recommended={"cpu_cores": new_cpu, "ram_gb": round(new_ram, 2)},
            monthly_saving_eur=saving,
            confidence=_confidence(
                _base_confidence(min(cpu_ratio, ram_ratio)), history
            ),
            effort="medium",
            patch=patch,
            **common,
        )
    ]


def _resources(
    namespace: str,
    data: dict[str, dict[str, float]],
    pod_cpu: float,
    pod_ram: float,
    new_cpu: float,
    new_ram: float,
) -> dict:
    """
    Requests and limits of the app container. A limit keeps its ratio to the
    request (never below 1); memory also covers the 14-day peak, since the
    static template sets its limit equal to the request.
    """
    cpu_limit = data["app_cpu_limit"].get(namespace)
    ram_limit = data["app_ram_limit"].get(namespace)
    peak = data["app_ram_peak"].get(namespace, 0.0) / 2**30
    resources = {"requests": k8s_resources(new_cpu, new_ram)}
    limits = k8s_resources(
        cpu_limit and new_cpu * max(1.0, cpu_limit / pod_cpu),
        ram_limit
        and max(
            new_ram * max(1.0, ram_limit / 2**30 / pod_ram),
            peak * RAM_HEADROOM,
        ),
    )
    if limits:
        resources["limits"] = limits
    return resources
