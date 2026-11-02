"""Monthly subscriptions from finops/saas.yaml, one line per month each."""

from datetime import date, datetime, time

from app.services.finops.ingest.projects import PLATFORM
from app.services.finops.ingest.store import CostLine

SOURCE = "saas"


def _next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def to_lines(month: date, subscriptions: list[dict]) -> list[CostLine]:
    start = datetime.combine(month.replace(day=1), time.min)
    end = datetime.combine(_next_month(month), time.min)
    lines = []
    for sub in subscriptions:
        if date.fromisoformat(str(sub["effective_from"])) > end.date():
            continue
        billed = float(sub.get("billed_eur_per_month", 0))
        listed = float(sub.get("list_eur_per_month", billed))
        lines.append(
            CostLine(
                x_source=SOURCE,
                provider_name=sub["provider"],
                service_name=sub["service"],
                service_category="SaaS",
                charge_period_start=start,
                charge_period_end=end,
                resource_id=f"saas/{sub['provider']}/{sub['service']}",
                resource_name=sub["service"],
                x_cost_eur=listed,
                billed_cost=billed,
                list_cost=listed,
                effective_cost=billed,
                project=PLATFORM,
                x_allocation_method="manual",
                x_pricing_basis="invoice",
                x_is_final=True,
            )
        )
    return lines
