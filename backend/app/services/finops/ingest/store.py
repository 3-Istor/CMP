"""
Write FOCUS cost lines idempotently. A line is identified by its source,
provider, resource, period start and charge category: ingesting the same day
again updates it in place. Closed months are never rewritten: a late revision
of one of their lines becomes an adjustment line on the first open month.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finops import CostLineItem, MonthClose

logger = logging.getLogger(__name__)


def day_window(day) -> tuple[datetime, datetime]:
    """UTC bounds of ``day``; today's window stops now (provisional data)."""
    start = datetime.combine(day, datetime.min.time())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return start, min(start + timedelta(days=1), now)


ADJUSTMENT = "Adjustment"


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


def _first_open_month(after: str, closed: set[str]) -> datetime:
    year, month = map(int, after.split("-"))
    while True:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        if f"{year:04d}-{month:02d}" not in closed:
            return datetime(year, month, 1)


def _adjust_closed_line(db: Session, line: CostLine, closed: set[str]) -> bool:
    """
    Book the revision of a closed-month line as an adjustment, and return
    whether the adjustment row changed. The adjustment always holds the whole
    difference between the revised cost and the stored original, so replaying
    the same revision leaves it as it is. A line the closed month never had
    has nothing to be compared with and is left out.
    """
    original = db.scalar(
        select(CostLineItem).where(
            CostLineItem.x_source == line.x_source,
            CostLineItem.provider_name == line.provider_name,
            CostLineItem.resource_id == line.resource_id,
            CostLineItem.charge_period_start == line.charge_period_start,
            CostLineItem.charge_category == line.charge_category,
        )
    )
    if original is None:
        return False
    delta = round(line.x_cost_eur - original.x_cost_eur, 9)
    adjustment_start = _first_open_month(line.billing_period, closed)
    # The day is part of the id: the adjustment identity has no other room
    # for it, and two days of one resource would otherwise collide.
    adjustment_id = (
        f"{line.resource_id}#adj-{line.charge_period_start:%Y-%m-%d}"
    )
    row = db.scalar(
        select(CostLineItem).where(
            CostLineItem.x_source == line.x_source,
            CostLineItem.provider_name == line.provider_name,
            CostLineItem.resource_id == adjustment_id,
            CostLineItem.charge_period_start == adjustment_start,
            CostLineItem.charge_category == ADJUSTMENT,
        )
    )
    if row is None:
        if delta == 0:
            return False
        row = CostLineItem(
            x_source=line.x_source,
            provider_name=line.provider_name,
            resource_id=adjustment_id,
            charge_period_start=adjustment_start,
            charge_category=ADJUSTMENT,
            billing_period=f"{adjustment_start:%Y-%m}",
        )
        db.add(row)
    elif row.x_cost_eur == delta:
        return False
    for column in _COLUMNS:
        setattr(row, column, getattr(line, column))
    row.charge_period_end = adjustment_start + (
        line.charge_period_end - line.charge_period_start
    )
    row.billed_cost = row.effective_cost = row.list_cost = delta
    row.billing_currency = "EUR"
    row.x_fx_rate = 1.0
    row.consumed_quantity = row.consumed_unit = None
    row.x_energy_kwh = row.x_co2e_kg = row.x_carbon_method = None
    row.x_cost_eur = delta
    row.x_is_final = True
    row.tags = {**line.tags(), "adjusts": f"{line.billing_period}"}
    row.x_ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return True


def upsert(db: Session, lines: list[CostLine]) -> int:
    """Insert or update ``lines``; return how many rows were written."""
    closed = set(db.scalars(select(MonthClose.billing_period)))
    written = skipped = 0
    for line in lines:
        if line.billing_period in closed:
            skipped += 1
            if _adjust_closed_line(db, line, closed):
                written += 1
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
    if skipped:
        logger.info(
            "FinOps: %d lines of closed months kept as they were", skipped
        )
    return written
