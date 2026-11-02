"""
What an app's measured usage would cost on each cluster (CNP-89): the
core-hours and GiB-hours OpenCost allocated to its namespace, priced at each
cluster's rates and brought to a month. Compute only: storage, network and
the platform fee are left out, so the figures compare where to run the app,
not its full price.

Lines ingested before quantities were recorded only carry a cost; for on-prem
lines the quantity is that cost divided by today's on-prem rate.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finops import CostLineItem
from app.services.finops.engine import AppSpec
from app.services.finops.ingest import inputs, opencost

WINDOW_DAYS = 30
HOURS_PER_MONTH = 730


@dataclass
class _Usage:
    core_hours: float = 0.0
    gib_hours: float = 0.0
    hours: float = 0.0
    clouds: set[str] | None = None


def _usage(
    db: Session,
    namespaces: set[str],
    start: datetime,
    end: datetime,
    onprem: inputs.NodeRates,
) -> dict[str, _Usage]:
    rows = db.execute(
        select(
            CostLineItem.resource_name,
            CostLineItem.resource_id,
            CostLineItem.cloud,
            CostLineItem.charge_period_start,
            CostLineItem.charge_period_end,
            CostLineItem.consumed_quantity,
            CostLineItem.x_cost_eur,
            CostLineItem.x_pricing_basis,
        ).where(
            CostLineItem.x_source == opencost.SOURCE,
            CostLineItem.charge_category == "Usage",
            CostLineItem.resource_name.in_(namespaces),
            CostLineItem.charge_period_start >= start,
            CostLineItem.charge_period_start < end,
        )
    ).all()
    out: dict[str, _Usage] = defaultdict(_Usage)
    windows: dict[str, set[tuple[datetime, datetime]]] = defaultdict(set)
    for name, resource, cloud, begin, finish, quantity, cost, basis in rows:
        kind = resource.rsplit("/", 1)[-1]
        if kind not in opencost.QUANTITIES:
            continue
        if quantity is None and basis == "tco":
            rate = (
                onprem.cpu_eur_per_core_hour
                if kind == "cpu"
                else onprem.ram_eur_per_gib_hour
            )
            quantity = (cost or 0.0) / rate if rate > 0 else 0.0
        usage = out[name]
        usage.clouds = (usage.clouds or set()) | {cloud}
        if kind == "cpu":
            usage.core_hours += quantity or 0.0
        else:
            usage.gib_hours += quantity or 0.0
        windows[name].add((begin, finish))
    for name, spans in windows.items():
        out[name].hours = sum(
            (finish - begin).total_seconds() / 3600 for begin, finish in spans
        )
    return out


def for_apps(
    db: Session,
    apps: list[AppSpec],
    today: date,
    usd_per_eur: float | None,
) -> list[dict]:
    rates = inputs.cluster_rates(usd_per_eur)
    # Today is provisional: a partial day would read as a quiet one.
    end = datetime.combine(today, time.min)
    start = end - timedelta(days=WINDOW_DAYS)
    by_namespace = {a.namespace: a for a in apps if a.namespace}
    usage = _usage(db, set(by_namespace), start, end, rates["onprem"])
    rows = []
    for namespace, app in by_namespace.items():
        measured = usage.get(namespace)
        if measured is None or measured.hours <= 0:
            continue
        cores = measured.core_hours / measured.hours
        gib = measured.gib_hours / measured.hours
        rows.append(
            {
                "app_id": app.id,
                "app_name": app.name,
                "project_id": app.project_id,
                "measured_on": sorted(measured.clouds or {"onprem"}),
                "avg_cores": round(cores, 3),
                "avg_ram_gib": round(gib, 3),
                "hours_measured": round(measured.hours, 1),
                "monthly_eur": {
                    cluster: round(
                        rate.hourly_eur(cores, gib) * HOURS_PER_MONTH, 2
                    )
                    for cluster, rate in rates.items()
                },
                "estimated": {
                    cluster: rate.estimated for cluster, rate in rates.items()
                },
            }
        )
    rows.sort(key=lambda r: max(r["monthly_eur"].values()), reverse=True)
    return rows
