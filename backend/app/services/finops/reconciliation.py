"""
Does the cloud invoice add up to what the projects are shown? ``reconcile``
splits one invoice into allocated, idle, platform and unallocated parts and
reports any gap; ``tag_compliance`` lists the biggest untagged resources.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finops import CostLineItem
from app.services.finops.ingest.focus import CLUSTER
from app.services.finops.ingest.projects import IDLE, PLATFORM
from app.services.finops.ingest.split import (
    invoice_source,
    split_days,
    split_source,
)

PROVIDERS = ("aws", "gcp")
_DECIMALS = 6


def _check(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown cloud provider: {provider}")


def reconcile(db: Session, provider: str, billing_period: str) -> dict:
    """
    Invoice total = Σ ``<provider>_focus`` lines. The parts are computed from
    the lines that count once: invoice lines, except ``__cluster__`` nodes of
    split days, plus the split lines that replace them. The gap is what the
    parts fail to explain; it is 0 unless lines are missing or inconsistent.
    """
    _check(provider)
    replaced = split_days(db, provider, billing_period)
    rows = db.execute(
        select(
            CostLineItem.x_source,
            CostLineItem.project,
            CostLineItem.charge_period_start,
            CostLineItem.x_cost_eur,
        ).where(
            CostLineItem.x_source.in_(
                [invoice_source(provider), split_source(provider)]
            ),
            CostLineItem.billing_period == billing_period,
        )
    ).all()

    invoice = allocated = idle = platform = unallocated = 0.0
    for source, project, start, cost in rows:
        if source == invoice_source(provider):
            invoice += cost
            if project == CLUSTER and start in replaced:
                continue
        if project is None or project == CLUSTER:
            unallocated += cost
        elif project == IDLE:
            idle += cost
        elif project == PLATFORM:
            platform += cost
        else:
            allocated += cost

    gap = invoice - (allocated + idle + platform + unallocated)
    return {
        "invoice_total_eur": round(invoice, _DECIMALS),
        "allocated_eur": round(allocated, _DECIMALS),
        "idle_eur": round(idle, _DECIMALS),
        "platform_eur": round(platform, _DECIMALS),
        "unallocated_eur": round(unallocated, _DECIMALS),
        "unallocated_pct": (
            round(100 * unallocated / invoice, 2) if invoice else 0.0
        ),
        "gap_eur": round(gap, _DECIMALS),
    }


def tag_compliance(
    db: Session, provider: str, billing_period: str, limit: int = 20
) -> list[dict]:
    """Costliest resources of the period carrying no project tag. Cluster
    nodes are left out: they are untagged by design and split by OpenCost."""
    _check(provider)
    rows = db.execute(
        select(
            CostLineItem.resource_id,
            CostLineItem.resource_name,
            CostLineItem.service_name,
            CostLineItem.x_cost_eur,
        ).where(
            CostLineItem.x_source == invoice_source(provider),
            CostLineItem.billing_period == billing_period,
            CostLineItem.project.is_(None),
        )
    ).all()
    costs: dict[str, float] = defaultdict(float)
    names: dict[str, tuple[str, str]] = {}
    for resource_id, name, service, cost in rows:
        costs[resource_id] += cost
        names.setdefault(resource_id, (name, service))
    total = sum(costs.values())
    ranked = sorted(costs.items(), key=lambda item: item[1], reverse=True)
    return [
        {
            "resource_id": resource_id,
            "resource_name": names[resource_id][0],
            "service_name": names[resource_id][1],
            "cost_eur": round(cost, _DECIMALS),
            "share_of_untagged_pct": (
                round(100 * cost / total, 2) if total > 0 else 0.0
            ),
        }
        for resource_id, cost in ranked[:limit]
        if cost > 0
    ]
