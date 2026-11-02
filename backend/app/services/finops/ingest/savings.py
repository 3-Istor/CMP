"""
Daily savings of offhours-guard, per namespace and cluster (D-F5).

Avoided: the requested CPU and memory of the namespace, priced at its
cluster's rates (on-prem TCO, cloud node list price), for the hours its
workloads slept. Realized on-prem: the electricity its busy cores would have
drawn meanwhile. In the cloud nothing is realized until a node autoscaler
removes nodes, so the ledger says 0.

A namespace's schedule usually covers all of its workloads, so the asleep
time is the mean over its targets. It is read from kube-state-metrics (a
target at 0 replicas sleeps) rather than from offhours_target_asleep, so
nights before the guard exposed metrics are counted too. It is counted in
hours with samples, not as a share of the day: a cloud cluster only runs
for demos, and the hours it was off are neither asleep nor awake.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finops import SavingsEntry
from app.services.finops.ingest import carbon
from app.services.finops.ingest.inputs import NodeRates, OnPremTCO
from app.services.finops.ingest.projects import owner_of
from app.services.finops.ingest.store import day_window
from app.services.metrics_isolation import VMSELECT, cluster_filter

CATEGORY = "offhours"
STEP_MINUTES = 5


@dataclass(frozen=True)
class NamespaceDay:
    namespace: str
    asleep_hours: float
    cpu_cores: float
    ram_gib: float
    awake_busy_cores: float


def _query(expr: str, at: datetime, cluster: str) -> dict[str, float]:
    response = httpx.get(
        f"{VMSELECT}/api/v1/query",
        params={
            "query": expr,
            "time": int(at.timestamp()),
            "extra_filters[]": cluster_filter(cluster),
        },
        timeout=30.0,
    )
    response.raise_for_status()
    out = {}
    for sample in response.json()["data"]["result"]:
        labels = sample["metric"]
        namespace = labels.get("exported_namespace") or labels.get("namespace")
        if namespace:
            out[namespace] = float(sample["value"][1])
    return out


# kube-state-metrics series and workload label per kind a schedule can target.
REPLICAS = {
    "Deployment": ("kube_deployment_spec_replicas", "deployment"),
    "StatefulSet": ("kube_statefulset_replicas", "statefulset"),
}


def targets() -> dict[str, dict[str, list[tuple[str, str]]]]:
    """
    Every workload a schedule targeted in the last 30 days, by cluster then
    namespace. Series from before the cluster label existed are on-prem.
    """
    response = httpx.get(
        f"{VMSELECT}/api/v1/query",
        params={
            "query": "group by (cluster, exported_namespace, target) "
            "(last_over_time(offhours_target_asleep[30d]))",
            "time": int(datetime.now(timezone.utc).timestamp()),
        },
        timeout=30.0,
    )
    response.raise_for_status()
    out: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for sample in response.json()["data"]["result"]:
        labels = sample["metric"]
        kind, _, name = labels.get("target", "").partition("/")
        # Alloy's pod scrape moves the controller's label to exported_namespace.
        namespace = labels.get("exported_namespace")
        cluster = labels.get("cluster") or "onprem"
        if namespace and name and kind in REPLICAS:
            workloads = out.setdefault(cluster, {}).setdefault(namespace, [])
            if (kind, name) not in workloads:
                workloads.append((kind, name))
    return out


def asleep_query(by_namespace: dict[str, list[tuple[str, str]]]) -> str:
    selectors = []
    for namespace, workloads in sorted(by_namespace.items()):
        for kind, name in sorted(workloads):
            metric, label = REPLICAS[kind]
            selectors.append(
                f'label_replace({metric}{{namespace="{namespace}",'
                f'{label}="{name}"}}, "workload", "$1", "{label}", "(.+)")'
            )
    replicas = " or ".join(selectors)
    return (
        "avg by (namespace) (sum_over_time(("
        f"sum by (namespace, workload) ({replicas}) == bool 0"
        f")[1d:{STEP_MINUTES}m])) * {STEP_MINUTES} / 60"
    )


def queries(by_namespace: dict[str, list[tuple[str, str]]]) -> dict[str, str]:
    requests = "kube_pod_container_resource_requests"
    return {
        "asleep_hours": asleep_query(by_namespace),
        # Asleep pods request nothing, so the day's peak is the awake size.
        "cpu_cores": "max_over_time((sum by (namespace) "
        f'({requests}{{resource="cpu"}}))[1d:15m])',
        "ram_gib": "max_over_time((sum by (namespace) "
        f'({requests}{{resource="memory"}}) / 2^30)[1d:15m])',
        "busy": "avg_over_time((sum by (namespace) "
        '(rate(container_cpu_usage_seconds_total{container!=""}[5m])))[1d:15m])',
    }


def fetch_day(
    day: date,
    cluster: str,
    by_namespace: dict[str, list[tuple[str, str]]],
) -> list[NamespaceDay]:
    if not by_namespace:
        return []
    _, end = day_window(day)
    results = {
        name: _query(expr, end, cluster)
        for name, expr in queries(by_namespace).items()
    }
    rows = []
    for namespace, hours in results["asleep_hours"].items():
        if hours <= 0:
            continue
        awake = 1 - min(hours, 24) / 24
        rows.append(
            NamespaceDay(
                namespace=namespace,
                asleep_hours=hours,
                cpu_cores=results["cpu_cores"].get(namespace, 0.0),
                ram_gib=results["ram_gib"].get(namespace, 0.0),
                awake_busy_cores=(
                    results["busy"].get(namespace, 0.0) / awake
                    if awake > 0
                    else 0.0
                ),
            )
        )
    return rows


def to_entries(
    day: date,
    rows: list[NamespaceDay],
    ns_projects: dict[str, str],
    rates: NodeRates,
    tco: OnPremTCO,
    g_per_kwh: float | None = None,
    cloud: str = "onprem",
) -> list[SavingsEntry]:
    entries = []
    for row in rows:
        energy_kwh = (
            row.asleep_hours
            * row.awake_busy_cores
            * tco.watts_per_busy_core
            / 1000
            if cloud == "onprem"
            else 0.0
        )
        owner = owner_of(row.namespace, ns_projects)
        entries.append(
            SavingsEntry(
                day=day,
                project=owner.project,
                app=owner.app or "",
                category=CATEGORY,
                avoided_eur=row.asleep_hours
                * rates.hourly_eur(row.cpu_cores, row.ram_gib),
                realized_eur=energy_kwh * tco.eur_per_kwh,
                energy_avoided_kwh=energy_kwh,
                co2e_avoided_kg=(
                    carbon.co2e_kg(energy_kwh, g_per_kwh)
                    if g_per_kwh is not None
                    else 0.0
                ),
                cloud=cloud,
            )
        )
    return entries


def store(db: Session, entries: list[SavingsEntry]) -> int:
    for entry in entries:
        row = db.scalar(
            select(SavingsEntry).where(
                SavingsEntry.day == entry.day,
                SavingsEntry.project == entry.project,
                SavingsEntry.app == entry.app,
                SavingsEntry.category == entry.category,
                SavingsEntry.cloud == entry.cloud,
            )
        )
        if row is None:
            db.add(entry)
            continue
        for column in (
            "avoided_eur",
            "realized_eur",
            "energy_avoided_kwh",
            "co2e_avoided_kg",
        ):
            setattr(row, column, getattr(entry, column))
    db.commit()
    return len(entries)
