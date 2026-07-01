"""
CostProvider abstraction.

The router and the alert poller talk only to a :class:`CostProvider`, never to
the simulation internals. Today the only implementation is
:class:`SimulatedCostProvider`; to move to real metrics later, implement a
``PrometheusCostProvider`` and change :func:`get_cost_provider` — nothing else
in the codebase needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, timedelta

from app.services.finops import engine, pricing
from app.services.finops.engine import AppSpec
from app.services.finops.recommendations import (
    Recommendation,
    recommendations_for_apps,
)


class CostProvider(ABC):
    @abstractmethod
    def specs(self, deployments: list) -> list[AppSpec]:
        ...

    @abstractmethod
    def timeline(self, apps: list[AppSpec], start: date, end: date,
                 granularity: str) -> list[dict]:
        ...

    @abstractmethod
    def breakdown(self, apps: list[AppSpec], ref: date | None = None) -> dict[str, float]:
        ...

    @abstractmethod
    def summary(self, apps: list[AppSpec], ref: date | None = None) -> dict:
        ...

    @abstractmethod
    def app_rows(self, apps: list[AppSpec], ref: date | None = None) -> list[dict]:
        ...

    @abstractmethod
    def recommendations(self, apps: list[AppSpec], ref: date | None = None) -> list[Recommendation]:
        ...


class SimulatedCostProvider(CostProvider):
    """Deterministic, time-based simulation (see :mod:`.engine`)."""

    def specs(self, deployments: list) -> list[AppSpec]:
        return [engine.app_spec_from_deployment(d) for d in deployments]

    def timeline(self, apps, start, end, granularity="daily"):
        daily = engine.combined_series(apps, start, end)
        return engine.aggregate_series(daily, granularity)

    def breakdown(self, apps, ref=None):
        return engine.breakdown(apps, ref)

    def summary(self, apps, ref=None):
        ref = ref or date.today()
        mtd = engine.month_to_date_cost(apps, ref)
        prev = engine.previous_month_cost(apps, ref)
        projected = engine.projected_month_cost(apps, ref)
        trend_pct = round((mtd - prev) / prev * 100, 1) if prev > 0 else None
        recs = self.recommendations(apps, ref)
        potential = round(sum(r.monthly_saving_eur for r in recs), 2)
        return {
            "month_to_date_eur": mtd,
            "projected_month_eur": projected,
            "previous_month_eur": prev,
            "trend_pct": trend_pct,
            "breakdown": engine.breakdown(apps, ref),
            "potential_savings_eur": potential,
            "app_count": len(apps),
            "currency": pricing.CURRENCY,
        }

    def app_rows(self, apps, ref=None):
        ref = ref or date.today()
        rows: list[dict] = []
        for app in apps:
            yday, today, mtd = engine.app_daily_and_monthly(app, ref)
            if yday > 0:
                trend = round((today - yday) / yday * 100, 1)
            else:
                trend = None
            projected = engine.projected_month_cost([app], ref)
            rows.append({
                "app_id": app.id,
                "name": app.name,
                "project_id": app.project_id,
                "cost_per_day_eur": today,
                "cost_month_estimate_eur": projected,
                "month_to_date_eur": mtd,
                "trend_pct": trend,
            })
        rows.sort(key=lambda r: r["cost_month_estimate_eur"], reverse=True)
        return rows

    def recommendations(self, apps, ref=None):
        return recommendations_for_apps(apps, ref)


_provider: CostProvider | None = None


def get_cost_provider() -> CostProvider:
    """Return the process-wide cost provider (simulated by default)."""
    global _provider
    if _provider is None:
        _provider = SimulatedCostProvider()
    return _provider


def period_to_range(period: str, ref: date | None = None) -> tuple[date, date]:
    """Map a period token (7d/30d/90d) to a concrete [start, end] range."""
    ref = ref or date.today()
    days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)
    return ref - timedelta(days=days - 1), ref
