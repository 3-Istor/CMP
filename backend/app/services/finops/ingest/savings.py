"""
Daily savings of offhours-guard, per namespace (D-F5).

Avoided: the requested CPU and memory of the namespace, priced like OpenCost
prices them, for the time its workloads slept. Realized on-prem: the
electricity its busy cores would have drawn meanwhile. In the cloud nothing is
realized until a node autoscaler removes nodes, so the ledger says 0.

A namespace's schedule usually covers all of its workloads, so the asleep
fraction is the mean over its targets. It is read from kube-state-metrics
(a target at 0 replicas sleeps) rather than from offhours_target_asleep, so
nights before the guard exposed metrics are counted too.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finops import SavingsEntry
from app.services.finops.ingest import carbon
from app.services.finops.ingest.inputs import OnPremTCO
from app.services.finops.ingest.projects import owner_of
from app.services.finops.ingest.store import day_window
from app.services.metrics_isolation import VMSELECT

CATEGORY = "offhours"


@dataclass(frozen=True)
class NamespaceDay:
    namespace: str
    asleep_fraction: float
    requested_eur_per_hour: float
    awake_busy_cores: float


def _query(expr: str, at: datetime) -> dict[str, float]:
    response = httpx.get(
        f"{VMSELECT}/api/v1/query",
        params={"query": expr, "time": int(at.timestamp())},
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


def targets() -> dict[str, list[tuple[str, str]]]:
    """Every workload a schedule targeted in the last 30 days, by namespace."""
    response = httpx.get(
        f"{VMSELECT}/api/v1/query",
        params={
            "query": "group by (exported_namespace, target) "
            "(last_over_time(offhours_target_asleep[30d]))",
            "time": int(datetime.now(timezone.utc).timestamp()),
        },
        timeout=30.0,
    )
    response.raise_for_status()
    out: dict[str, list[tuple[str, str]]] = {}
    for sample in response.json()["data"]["result"]:
        labels = sample["metric"]
        kind, _, name = labels.get("target", "").partition("/")
        # Alloy's pod scrape moves the controller's label to exported_namespace.
        namespace = labels.get("exported_namespace")
        if namespace and name and kind in REPLICAS:
            out.setdefault(namespace, []).append((kind, name))
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
        "avg by (namespace) (avg_over_time(("
        f"sum by (namespace, workload) ({replicas}) == bool 0"
        ")[1d:5m]))"
    )


def queries(
    tco: OnPremTCO, by_namespace: dict[str, list[tuple[str, str]]]
) -> dict[str, str]:
    requested = (
        'sum by (namespace) (kube_pod_container_resource_requests{resource="cpu"})'
        f" * {tco.cpu_eur_per_core_hour:.10f}"
        " + sum by (namespace) "
        '(kube_pod_container_resource_requests{resource="memory"}) / 2^30'
        f" * {tco.ram_eur_per_gib_hour:.10f}"
    )
    return {
        "asleep": asleep_query(by_namespace),
        # Asleep pods request nothing, so the day's peak is the awake size.
        "requested": f"max_over_time(({requested})[1d:15m])",
        "busy": "avg_over_time((sum by (namespace) "
        '(rate(container_cpu_usage_seconds_total{container!=""}[5m])))[1d:15m])',
    }


def fetch_day(day: date, tco: OnPremTCO) -> list[NamespaceDay]:
    by_namespace = targets()
    if not by_namespace:
        return []
    _, end = day_window(day)
    q = queries(tco, by_namespace)
    asleep = _query(q["asleep"], end)
    requested = _query(q["requested"], end)
    busy = _query(q["busy"], end)
    rows = []
    for namespace, fraction in asleep.items():
        if fraction <= 0:
            continue
        awake = 1 - fraction
        rows.append(
            NamespaceDay(
                namespace=namespace,
                asleep_fraction=fraction,
                requested_eur_per_hour=requested.get(namespace, 0.0),
                awake_busy_cores=(
                    busy.get(namespace, 0.0) / awake if awake > 0 else 0.0
                ),
            )
        )
    return rows


def to_entries(
    day: date,
    rows: list[NamespaceDay],
    ns_projects: dict[str, str],
    tco: OnPremTCO,
    g_per_kwh: float | None = None,
) -> list[SavingsEntry]:
    entries = []
    for row in rows:
        asleep_hours = row.asleep_fraction * 24
        energy_kwh = (
            asleep_hours
            * row.awake_busy_cores
            * tco.watts_per_busy_core
            / 1000
        )
        owner = owner_of(row.namespace, ns_projects)
        entries.append(
            SavingsEntry(
                day=day,
                project=owner.project,
                app=owner.app or "",
                category=CATEGORY,
                avoided_eur=asleep_hours * row.requested_eur_per_hour,
                realized_eur=energy_kwh * tco.eur_per_kwh,
                energy_avoided_kwh=energy_kwh,
                co2e_avoided_kg=(
                    carbon.co2e_kg(energy_kwh, g_per_kwh)
                    if g_per_kwh is not None
                    else 0.0
                ),
                cloud="onprem",
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
            "cloud",
        ):
            setattr(row, column, getattr(entry, column))
    db.commit()
    return len(entries)
