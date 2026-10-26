"""
Write FOCUS cost lines idempotently. A line is identified by its source,
provider, resource, period start and charge category: ingesting the same day
again updates it in place. Closed months are never rewritten.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finops import CostLineItem, MonthClose

logger = logging.getLogger(__name__)


@dataclass
class CostLine:
    x_source: str
    provider_name: str
    service_name: str
    service_category: str
    charge_period_start: datetime
    charge_period_end: datetime
    resource_id: str
    x_cost_eur: float
    billed_cost: float
    list_cost: float
    effective_cost: float
    billing_currency: str = "EUR"
    x_fx_rate: float = 1.0
    charge_category: str = "Usage"
    resource_name: str = ""
    consumed_quantity: float | None = None
    consumed_unit: str | None = None
    project: str | None = None
    app: str | None = None
    env: str | None = None
    cloud: str = "onprem"
    x_allocation_method: str = "direct"
    x_pricing_basis: str = "invoice"
    x_energy_kwh: float | None = None
    x_co2e_kg: float | None = None
    x_carbon_method: str | None = None
    x_is_final: bool = False
    x_is_estimate: bool = False
    extra_tags: dict = field(default_factory=dict)

    @property
    def billing_period(self) -> str:
        return self.charge_period_start.strftime("%Y-%m")

    def tags(self) -> dict:
        tags = {
            "cnp_project": self.project,
            "cnp_app": self.app,
            "cnp_env": self.env,
        }
        tags.update(self.extra_tags)
        return {k: v for k, v in tags.items() if v is not None}


_COLUMNS = (
    "service_name",
    "service_category",
    "charge_period_end",
    "resource_name",
    "billed_cost",
    "effective_cost",
    "list_cost",
    "billing_currency",
    "consumed_quantity",
    "consumed_unit",
    "project",
    "app",
    "env",
    "cloud",
    "x_cost_eur",
    "x_fx_rate",
    "x_allocation_method",
    "x_pricing_basis",
    "x_energy_kwh",
    "x_co2e_kg",
    "x_carbon_method",
    "x_is_final",
    "x_is_estimate",
)


def upsert(db: Session, lines: list[CostLine]) -> int:
    """Insert or update ``lines``; return how many rows were written."""
    closed = set(db.scalars(select(MonthClose.billing_period)))
    written = 0
    for line in lines:
        if line.billing_period in closed:
            logger.info(
                "FinOps: %s %s skipped, %s is closed",
                line.x_source,
                line.resource_id,
                line.billing_period,
            )
            continue
        row = db.scalar(
            select(CostLineItem).where(
                CostLineItem.x_source == line.x_source,
                CostLineItem.provider_name == line.provider_name,
                CostLineItem.resource_id == line.resource_id,
                CostLineItem.charge_period_start == line.charge_period_start,
                CostLineItem.charge_category == line.charge_category,
            )
        )
        if row is None:
            row = CostLineItem(
                x_source=line.x_source,
                provider_name=line.provider_name,
                resource_id=line.resource_id,
                charge_period_start=line.charge_period_start,
                charge_category=line.charge_category,
                billing_period=line.billing_period,
            )
            db.add(row)
        for column in _COLUMNS:
            setattr(row, column, getattr(line, column))
        row.tags = line.tags()
        row.x_ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
        written += 1
    db.commit()
    return written
