"""
Daily allocations of the on-prem cluster from OpenCost, one FOCUS line per
namespace and resource. OpenCost prices them from the TCO (custom pricing), so
the amount is the on-prem cost itself, not a share of an invoice.
"""

from datetime import date, datetime, time, timedelta

import httpx

from app.core.config import settings
from app.services.finops.ingest.inputs import OnPremTCO
from app.services.finops.ingest.projects import owner_of
from app.services.finops.ingest.store import CostLine, day_window

SOURCE = "opencost"
PROVIDER = "CNP on-prem"
CLUSTER = "onprem"

# (OpenCost field, resource suffix, FOCUS ServiceName, ServiceCategory)
COMPONENTS = (
    ("cpuCost", "cpu", "Kubernetes CPU", "Compute"),
    ("gpuCost", "gpu", "Kubernetes GPU", "Compute"),
    ("ramCost", "ram", "Kubernetes memory", "Compute"),
    ("pvCost", "storage", "Persistent volumes", "Storage"),
    ("networkCost", "network", "Network", "Networking"),
    ("loadBalancerCost", "lb", "Load balancers", "Networking"),
)

# What a resource line consumed: (OpenCost field, divisor, FOCUS ConsumedUnit).
QUANTITIES = {
    "cpu": ("cpuCoreHours", 1, "CoreHour"),
    "ram": ("ramByteHours", 2**30, "GiBHour"),
}


def clusters() -> dict[str, str]:
    """OpenCost API of each cluster: on-prem plus the clouds that are set."""
    urls = {CLUSTER: settings.OPENCOST_URL}
    for item in settings.FINOPS_OPENCOST_CLUSTERS.split(","):
        cloud, _, url = item.partition("=")
        if cloud.strip() and url.strip():
            urls[cloud.strip()] = url.strip()
    return urls


def fetch_day(day: date, base_url: str | None = None) -> dict[str, dict]:
    start, end = day_window(day)
    response = httpx.get(
        f"{base_url or settings.OPENCOST_URL}/allocation",
        params={
            "window": f"{start:%Y-%m-%dT%H:%M:%SZ},{end:%Y-%m-%dT%H:%M:%SZ}",
            "aggregate": "namespace",
            "accumulate": "true",
            "includeIdle": "true",
            "shareIdle": "false",
        },
        timeout=60.0,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 200 or not body.get("data"):
        raise ValueError(f"OpenCost answered {body.get('code')}")
    return body["data"][0] or {}


def to_lines(
    day: date,
    allocations: dict[str, dict],
    ns_projects: dict[str, str],
    tco: OnPremTCO,
    is_final: bool,
    cloud: str = CLUSTER,
) -> list[CostLine]:
    """
    On-prem lines are the cost itself (TCO prices). Cloud lines carry
    OpenCost's list prices in USD and only serve as shares to split the
    invoice (ingest/split.py); totals never count them.
    """
    onprem = cloud == CLUSTER
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    lines: list[CostLine] = []
    for namespace, alloc in allocations.items():
        owner = owner_of(namespace, ns_projects)
        for field, suffix, service, category in COMPONENTS:
            cost = float(alloc.get(field) or 0.0)
            if cost <= 0:
                continue
            energy_kwh = None
            if onprem and tco.eur_per_kwh > 0:
                energy_kwh = cost * tco.energy_share / tco.eur_per_kwh
            quantity = unit = None
            if suffix in QUANTITIES:
                field_name, divisor, unit = QUANTITIES[suffix]
                quantity = float(alloc.get(field_name) or 0.0) / divisor
            lines.append(
                CostLine(
                    x_source=SOURCE,
                    provider_name=PROVIDER if onprem else f"OpenCost {cloud}",
                    service_name=service,
                    service_category=category,
                    charge_period_start=start,
                    charge_period_end=end,
                    resource_id=f"{cloud}/{namespace}/{suffix}",
                    resource_name=namespace,
                    x_cost_eur=cost,
                    billed_cost=cost,
                    list_cost=cost,
                    effective_cost=cost,
                    project=owner.project,
                    app=owner.app,
                    billing_currency="EUR" if onprem else "USD",
                    cloud=cloud,
                    x_allocation_method=owner.method,
                    x_pricing_basis="tco" if onprem else "list_price",
                    x_energy_kwh=energy_kwh,
                    consumed_quantity=quantity,
                    consumed_unit=unit,
                    x_is_final=is_final,
                    x_is_estimate=tco.has_mock if onprem else False,
                    extra_tags={"k8s_namespace": namespace},
                )
            )
    return lines
