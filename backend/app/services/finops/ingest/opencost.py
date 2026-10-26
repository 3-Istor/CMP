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
from app.services.finops.ingest.store import CostLine

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


def fetch_day(day: date) -> dict[str, dict]:
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    response = httpx.get(
        f"{settings.OPENCOST_URL}/allocation",
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
) -> list[CostLine]:
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
            if tco.eur_per_kwh > 0:
                energy_kwh = cost * tco.energy_share / tco.eur_per_kwh
            lines.append(
                CostLine(
                    x_source=SOURCE,
                    provider_name=PROVIDER,
                    service_name=service,
                    service_category=category,
                    charge_period_start=start,
                    charge_period_end=end,
                    resource_id=f"{CLUSTER}/{namespace}/{suffix}",
                    resource_name=namespace,
                    x_cost_eur=cost,
                    billed_cost=cost,
                    list_cost=cost,
                    effective_cost=cost,
                    project=owner.project,
                    app=owner.app,
                    cloud=CLUSTER,
                    x_allocation_method=owner.method,
                    x_pricing_basis="tco",
                    x_energy_kwh=energy_kwh,
                    x_is_final=is_final,
                    x_is_estimate=tco.has_mock,
                    extra_tags={"k8s_namespace": namespace},
                )
            )
    return lines
