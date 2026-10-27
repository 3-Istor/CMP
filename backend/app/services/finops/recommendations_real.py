"""
Recommendations from measured usage in VictoriaMetrics, never from the
simulation: CPU p95 and memory p99 over 14 days against what the namespace
requests, and namespaces that received no traffic for 7 days.

Savings are priced with the on-prem cost per core-hour and GiB-hour that the
TCO inputs give OpenCost (``onprem-tco.yaml``). The TCO file is versioned and
mounted next to the ingester, so it needs no extra scrape; OpenCost's
``node_*_hourly_cost`` metrics carry the same numbers behind one more
dependency.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import date

import httpx

from app.services import metrics_isolation
from app.services.finops.engine import AppSpec
from app.services.finops.ingest import inputs
from app.services.finops.recommendations import Recommendation

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

_cache: dict[tuple[str, ...], tuple[float, dict]] = {}


def _instant(query: str) -> dict[str, float]:
    response = httpx.get(
        f"{metrics_isolation.VMSELECT}/api/v1/query",
        params={
            "query": query,
            "extra_filters[]": metrics_isolation.ONPREM_FILTER,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return {
        r["metric"].get("namespace", ""): float(r["value"][1])
        for r in response.json()["data"]["result"]
    }


def _fetch(namespaces: tuple[str, ...]) -> dict[str, dict[str, float]]:
    cached = _cache.get(namespaces)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    ns = f'namespace=~"{"|".join(namespaces)}"'
    pods = f'{ns},container!="",container!="POD"'
    requests = "kube_pod_container_resource_requests"
    data = {
        "cpu_request": _instant(
            f'sum by (namespace) ({requests}{{{ns},resource="cpu"}})'
        ),
        "ram_request": _instant(
            f'sum by (namespace) ({requests}{{{ns},resource="memory"}})'
        ),
        "replicas": _instant(
            f"sum by (namespace) (kube_deployment_spec_replicas{{{ns}}})"
        ),
        "deployments": _instant(
            f"count by (namespace) (kube_deployment_spec_replicas{{{ns}}})"
        ),
        "cpu_p95": _instant(
            "quantile_over_time(0.95, sum by (namespace) "
            f"(rate(container_cpu_usage_seconds_total{{{pods}}}[5m]))"
            f"[{WINDOW}:30m])"
        ),
        "ram_p99": _instant(
            "quantile_over_time(0.99, sum by (namespace) "
            f"(container_memory_working_set_bytes{{{pods}}})[{WINDOW}:30m])"
        ),
        "history_days": _instant(
            "count_over_time(sum by (namespace) "
            f"(container_memory_working_set_bytes{{{pods}}})[{WINDOW}:1h]) / 24"
        ),
        "network_bytes": _instant(
            "sum by (namespace) (increase("
            f"container_network_receive_bytes_total{{{ns}}}"
            f"[{INACTIVITY_WINDOW}]))"
        ),
    }
    _cache[namespaces] = (time.monotonic(), data)
    return data


def _confidence(base: int, history_days: float) -> int:
    covered = min(1.0, history_days / FULL_HISTORY_DAYS)
    return int(max(10, base * (0.4 + 0.6 * covered)))


def _base_confidence(ratio: float) -> int:
    return int(max(60, min(98, 98 - ratio / OVERSIZED_RATIO * 38)))


def _monthly_eur(cpu_cores: float, ram_gib: float) -> float:
    tco = inputs.onprem_tco()
    hourly = (
        cpu_cores * tco.cpu_eur_per_core_hour
        + ram_gib * tco.ram_eur_per_gib_hour
    )
    return hourly * HOURS_PER_MONTH


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
        data = _fetch(tuple(sorted(by_namespace)))
        recs: list[Recommendation] = []
        for namespace, app in by_namespace.items():
            recs.extend(_for_namespace(app, namespace, data))
    except (httpx.HTTPError, KeyError, ValueError, OSError) as exc:
        logger.warning("FinOps recommendations unavailable: %s", exc)
        return []
    recs.sort(key=lambda r: r.monthly_saving_eur, reverse=True)
    return recs


def _for_namespace(
    app: AppSpec, namespace: str, data: dict[str, dict[str, float]]
) -> list[Recommendation]:
    cpu_req = data["cpu_request"].get(namespace, 0.0)
    ram_req = data["ram_request"].get(namespace, 0.0) / 2**30
    if cpu_req <= 0 and ram_req <= 0:
        return []
    history = data["history_days"].get(namespace, 0.0)
    common = {
        "app_id": app.id,
        "app_name": app.name,
        "project_id": app.project_id,
    }

    network = data["network_bytes"].get(namespace)
    if network is not None and network < INACTIVITY_MAX_BYTES and history >= 7:
        return [
            Recommendation(
                id=f"{app.id}:inactivity",
                rec_type="inactivity",
                title="Mettre en hibernation (inactivité)",
                justification=(
                    "Aucun trafic réseau entrant significatif depuis 7 jours."
                ),
                current={"cpu_cores": round(cpu_req, 2), "status": "running"},
                recommended={"replicas": 0, "status": "hibernated"},
                monthly_saving_eur=round(_monthly_eur(cpu_req, ram_req), 2),
                confidence=_confidence(80, history),
                effort="low",
                patch={"replicaCount": 0},
                **common,
            )
        ]

    cpu_p95 = data["cpu_p95"].get(namespace)
    ram_p99 = data["ram_p99"].get(namespace, 0.0) / 2**30
    if cpu_p95 is None or cpu_req <= 0:
        return []

    replicas = int(data["replicas"].get(namespace, 0))
    single_deployment = data["deployments"].get(namespace, 0) == 1
    cpu_ratio = cpu_p95 / cpu_req
    ram_ratio = ram_p99 / ram_req if ram_req > 0 else 1.0

    if single_deployment and replicas > 1 and cpu_ratio < OVERSIZED_RATIO:
        per_replica_cpu = cpu_req / replicas
        target = max(1, math.ceil(cpu_p95 / (per_replica_cpu * TARGET_LOAD)))
        if target < replicas:
            freed = replicas - target
            return [
                Recommendation(
                    id=f"{app.id}:replicas",
                    rec_type="replicas",
                    title="Réduire le nombre de replicas",
                    justification=(
                        f"Le CPU au p95 sur {WINDOW} représente "
                        f"{cpu_ratio * 100:.0f}% des requests (< 50%)."
                    ),
                    current={"replicas": replicas},
                    recommended={"replicas": target},
                    monthly_saving_eur=round(
                        _monthly_eur(
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

    if cpu_ratio >= OVERSIZED_RATIO and ram_ratio >= OVERSIZED_RATIO:
        return []
    new_cpu = cpu_req
    new_ram = ram_req
    if cpu_ratio < OVERSIZED_RATIO:
        new_cpu = max(0.05, round(cpu_p95 * CPU_HEADROOM, 2))
    if ram_ratio < OVERSIZED_RATIO:
        new_ram = max(0.05, round(ram_p99 * RAM_HEADROOM, 2))
    saving = round(
        _monthly_eur(max(0.0, cpu_req - new_cpu), max(0.0, ram_req - new_ram)),
        2,
    )
    if saving <= 0:
        return []
    return [
        Recommendation(
            id=f"{app.id}:rightsizing",
            rec_type="rightsizing",
            title="Ajuster les requests CPU / RAM",
            justification=(
                f"Sur {WINDOW}, le CPU au p95 est à {cpu_ratio * 100:.0f}% "
                f"et la mémoire au p99 à {ram_ratio * 100:.0f}% des requests."
            ),
            current={
                "cpu_cores": round(cpu_req, 2),
                "ram_gb": round(ram_req, 2),
            },
            recommended={"cpu_cores": new_cpu, "ram_gb": new_ram},
            monthly_saving_eur=saving,
            confidence=_confidence(
                _base_confidence(min(cpu_ratio, ram_ratio)), history
            ),
            effort="medium",
            patch=None,
            **common,
        )
    ]
