"""
CostProvider abstraction.

The router and the alert poller talk only to a :class:`CostProvider`, never to
the simulation internals. :class:`SimulatedCostProvider` is the default;
:class:`RealCostProvider` reads the ingested ``cost_line_items``.
:func:`get_cost_provider` picks one from ``settings.FINOPS_PROVIDER``.
"""

from __future__ import annotations

import calendar
import zlib
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.finops import CostLineItem
from app.services.finops import engine, platform, pricing, recommendations_real
from app.services.finops.engine import AppSpec
from app.services.finops.recommendations import (
    Recommendation,
    recommendations_for_apps,
)


class CostProvider(ABC):
    @abstractmethod
    def specs(self, deployments: list) -> list[AppSpec]: ...

    @abstractmethod
    def timeline(
        self, apps: list[AppSpec], start: date, end: date, granularity: str
    ) -> list[dict]: ...

    @abstractmethod
    def breakdown(
        self, apps: list[AppSpec], ref: date | None = None
    ) -> dict[str, float]: ...

    @abstractmethod
    def summary(
        self, apps: list[AppSpec], ref: date | None = None
    ) -> dict: ...

    @abstractmethod
    def app_rows(
        self, apps: list[AppSpec], ref: date | None = None
    ) -> list[dict]: ...

    @abstractmethod
    def recommendations(
        self, apps: list[AppSpec], ref: date | None = None
    ) -> list[Recommendation]: ...

    def direct_timeline(
        self, apps: list[AppSpec], start: date, end: date
    ) -> list[dict]:
        """Daily cost without the platform fee (equal to ``timeline`` here)."""
        return self.timeline(apps, start, end, "daily")

    def by_cloud(
        self, apps: list[AppSpec], ref: date | None = None
    ) -> dict[str, float]:
        """Month-to-date cost per cloud; everything is on-prem by default."""
        total = self.summary(apps, ref)["month_to_date_eur"]
        return {"onprem": total, "aws": 0.0, "gcp": 0.0}


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
            "app_count": sum(
                a.namespace != platform.UNCOVERED_NAMESPACE for a in apps
            ),
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
            rows.append(
                {
                    "app_id": app.id,
                    "name": app.name,
                    "project_id": app.project_id,
                    "cost_per_day_eur": today,
                    "cost_month_estimate_eur": projected,
                    "month_to_date_eur": mtd,
                    "trend_pct": trend,
                }
            )
        rows.sort(key=lambda r: r["cost_month_estimate_eur"], reverse=True)
        return rows

    def recommendations(self, apps, ref=None):
        return recommendations_for_apps(apps, ref)


REAL_RESOURCES = ("cpu", "ram", "storage", "network", "ai", "platform")
_CLOUDS = ("onprem", "aws", "gcp")
_BY_SERVICE = {
    "Kubernetes CPU": "cpu",
    "Kubernetes GPU": "cpu",
    "Kubernetes memory": "ram",
    "Persistent volumes": "storage",
    "Network": "network",
    "Load balancers": "network",
}
_BY_CATEGORY = {
    "AI and Machine Learning": "ai",
    "Storage": "storage",
    "Networking": "network",
}
_NOT_A_PROJECT = platform.NOT_A_PROJECT


def resource_of(service_name: str, service_category: str) -> str:
    """Breakdown key of a cost line; unknown compute-like lines count as cpu."""
    return _BY_SERVICE.get(service_name) or _BY_CATEGORY.get(
        service_category, "cpu"
    )


def _days_in_month(ref: date) -> int:
    return calendar.monthrange(ref.year, ref.month)[1]


class RealCostProvider(CostProvider):
    """
    Costs ingested into ``cost_line_items``. An application owns the OpenCost
    lines of its namespace; AI gateway and cloud lines are attributed to the
    projects of the applications asked for. Data stops at yesterday.
    """

    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory

    def specs(self, deployments):
        return [engine.app_spec_from_deployment(d) for d in deployments]

    def specs_all(self, deployments, ref: date | None = None) -> list[AppSpec]:
        """
        Every project namespace with costs in the last 90 days, for the
        platform-wide view: CMP apps keep their id, apps that predate the CMP
        get a stable negative id. Platform namespaces and idle capacity are
        not apps: they are carried by the fees and by one synthetic row for
        the part no fee recovers, so the rows still add up to the cluster.
        """
        ref = ref or date.today()
        specs = self.specs(deployments)
        known = {s.namespace for s in specs if s.namespace}
        query = (
            select(CostLineItem.resource_name, CostLineItem.project)
            .where(
                CostLineItem.x_source == "opencost",
                CostLineItem.cloud == "onprem",
                CostLineItem.charge_period_start
                >= datetime.combine(
                    ref - timedelta(days=90), datetime.min.time()
                ),
            )
            .distinct()
        )
        with self._session_factory() as db:
            rows = db.execute(query).all()
        created = datetime.combine(ref, datetime.min.time())
        empty = engine.Quota(0, 0.0, 0.0, 0.0, 0.0)
        for namespace, project in sorted(
            rows, key=lambda r: (r[0], r[1] or "")
        ):
            if namespace in known or project in _NOT_A_PROJECT:
                continue
            known.add(namespace)
            specs.append(
                AppSpec(
                    id=-(zlib.crc32(namespace.encode()) or 1),
                    name=namespace,
                    project_id=project,
                    created_at=created,
                    quota=empty,
                    namespace=namespace,
                )
            )
        specs.append(
            AppSpec(
                id=-(zlib.crc32(platform.UNCOVERED_NAMESPACE.encode()) or 1),
                name=platform.UNCOVERED_NAME,
                project_id="platform",
                created_at=created,
                quota=empty,
                namespace=platform.UNCOVERED_NAMESPACE,
            )
        )
        return specs

    def cluster_days(
        self, start: date, end: date
    ) -> dict[date, platform.ClusterDay]:
        """On-prem OpenCost cost of the whole cluster, one grouped query."""
        query = (
            select(
                CostLineItem.charge_period_start,
                CostLineItem.resource_name,
                CostLineItem.project,
                func.sum(CostLineItem.x_cost_eur),
            )
            .where(
                CostLineItem.x_source == "opencost",
                CostLineItem.cloud == "onprem",
                CostLineItem.charge_period_start
                >= datetime.combine(start, datetime.min.time()),
                CostLineItem.charge_period_start
                < datetime.combine(
                    end + timedelta(days=1), datetime.min.time()
                ),
            )
            .group_by(
                CostLineItem.charge_period_start,
                CostLineItem.resource_name,
                CostLineItem.project,
            )
        )
        with self._session_factory() as db:
            rows = db.execute(query).all()
        return platform.cluster_days(
            (when.date(), name, project, cost or 0.0)
            for when, name, project, cost in rows
        )

    def platform_report(self, start: date, end: date) -> dict:
        return platform.report(self.cluster_days(start, end), start, end)

    @staticmethod
    def _fees(
        apps, cluster: dict[date, platform.ClusterDay]
    ) -> dict[date, float]:
        own = {
            a.namespace
            for a in apps
            if a.namespace and a.namespace != platform.UNCOVERED_NAMESPACE
        }
        with_uncovered = any(
            a.namespace == platform.UNCOVERED_NAMESPACE for a in apps
        )
        fees: dict[date, float] = {}
        for day, slot in cluster.items():
            amount = slot.fee * len(own & slot.active.keys())
            if with_uncovered:
                amount += slot.uncovered
            if amount:
                fees[day] = amount
        return fees

    def _days(
        self, apps, start: date, end: date, cluster=None
    ) -> dict[date, dict[str, float]]:
        days = self._daily(self._lines(apps, start, end))
        if not any(a.namespace for a in apps):
            return days
        cluster = (
            cluster if cluster is not None else self.cluster_days(start, end)
        )
        for day, amount in self._fees(apps, cluster).items():
            days[day]["platform"] += amount
        return days

    def _lines(self, apps, start: date, end: date) -> list[tuple]:
        namespaces = {a.namespace for a in apps if a.namespace}
        projects = {
            a.project_id
            for a in apps
            if a.project_id and a.project_id not in _NOT_A_PROJECT
        }
        if not namespaces and not projects:
            return []
        namespaces |= {f"{p}-system" for p in projects}
        query = select(
            CostLineItem.charge_period_start,
            CostLineItem.resource_name,
            CostLineItem.service_name,
            CostLineItem.service_category,
            CostLineItem.cloud,
            CostLineItem.x_cost_eur,
        ).where(
            CostLineItem.charge_period_start
            >= datetime.combine(start, datetime.min.time()),
            CostLineItem.charge_period_start
            < datetime.combine(end + timedelta(days=1), datetime.min.time()),
            or_(
                (CostLineItem.x_source == "opencost")
                & (CostLineItem.cloud == "onprem")
                & CostLineItem.resource_name.in_(namespaces),
                # Cloud apps: their share of the invoice, split by OpenCost.
                CostLineItem.x_source.like("%\\_split", escape="\\")
                & CostLineItem.resource_name.in_(namespaces),
                (CostLineItem.x_source != "opencost")
                & CostLineItem.x_source.not_like("%\\_split", escape="\\")
                & CostLineItem.project.in_(projects),
            ),
        )
        with self._session_factory() as db:
            return [tuple(r) for r in db.execute(query).all()]

    @staticmethod
    def _daily(lines: list[tuple]) -> dict[date, dict[str, float]]:
        days: dict[date, dict[str, float]] = defaultdict(
            lambda: {r: 0.0 for r in REAL_RESOURCES}
        )
        for start, _name, service, category, _cloud, cost in lines:
            days[start.date()][resource_of(service, category)] += cost
        return days

    @staticmethod
    def _points(days: dict[date, dict[str, float]]) -> list[dict]:
        return [
            {
                "date": day.isoformat(),
                **{r: round(slot[r], 4) for r in REAL_RESOURCES},
                "total": round(sum(slot.values()), 4),
            }
            for day, slot in sorted(days.items())
        ]

    def timeline(self, apps, start, end, granularity="daily"):
        daily = self._points(self._days(apps, start, end))
        return engine.aggregate_series(daily, granularity, REAL_RESOURCES)

    def direct_timeline(self, apps, start, end):
        return self._points(self._daily(self._lines(apps, start, end)))

    def breakdown(self, apps, ref=None):
        ref = ref or date.today()
        totals = {r: 0.0 for r in REAL_RESOURCES}
        for slot in self._days(apps, ref.replace(day=1), ref).values():
            for r in REAL_RESOURCES:
                totals[r] += slot[r]
        return {r: round(v, 2) for r, v in totals.items()}

    def by_cloud(self, apps, ref=None):
        ref = ref or date.today()
        totals = {c: 0.0 for c in _CLOUDS}
        for *_head, cloud, cost in self._lines(apps, ref.replace(day=1), ref):
            totals[cloud] = totals.get(cloud, 0.0) + cost
        if any(a.namespace for a in apps):
            totals["onprem"] += sum(
                self._fees(
                    apps, self.cluster_days(ref.replace(day=1), ref)
                ).values()
            )
        return {c: round(v, 2) for c, v in totals.items()}

    def summary(self, apps, ref=None):
        ref = ref or date.today()
        first = ref.replace(day=1)
        last_prev = first - timedelta(days=1)
        days = self._days(apps, first, ref)
        mtd = sum(sum(slot.values()) for slot in days.values())
        prev = sum(
            sum(slot.values())
            for slot in self._days(
                apps, last_prev.replace(day=1), last_prev
            ).values()
        )
        projected = mtd / len(days) * _days_in_month(ref) if days else 0.0
        recs = self.recommendations(apps, ref)
        return {
            "month_to_date_eur": round(mtd, 2),
            "projected_month_eur": round(projected, 2),
            "previous_month_eur": round(prev, 2),
            "trend_pct": (
                round((mtd - prev) / prev * 100, 1) if prev > 0 else None
            ),
            "breakdown": self.breakdown(apps, ref),
            "potential_savings_eur": round(
                sum(r.monthly_saving_eur for r in recs), 2
            ),
            "app_count": len(apps),
            "currency": pricing.CURRENCY,
        }

    def app_rows(self, apps, ref=None):
        ref = ref or date.today()
        first = ref.replace(day=1)
        rows: list[dict] = []
        cluster = (
            self.cluster_days(first, ref)
            if any(a.namespace for a in apps)
            else {}
        )
        for app in apps:
            days = self._days([app], first, ref, cluster)
            totals = {d: sum(slot.values()) for d, slot in days.items()}
            ordered = sorted(totals)
            latest = totals[ordered[-1]] if ordered else 0.0
            before = totals[ordered[-2]] if len(ordered) > 1 else 0.0
            mtd = sum(totals.values())
            rows.append(
                {
                    "app_id": app.id,
                    "name": app.name,
                    "project_id": app.project_id,
                    "cost_per_day_eur": round(latest, 2),
                    "cost_month_estimate_eur": round(
                        (
                            mtd / len(totals) * _days_in_month(ref)
                            if totals
                            else 0.0
                        ),
                        2,
                    ),
                    "month_to_date_eur": round(mtd, 2),
                    "platform_eur": round(
                        sum(slot["platform"] for slot in days.values()), 2
                    ),
                    "trend_pct": (
                        round((latest - before) / before * 100, 1)
                        if before > 0
                        else None
                    ),
                }
            )
        rows.sort(key=lambda r: r["cost_month_estimate_eur"], reverse=True)
        return rows

    def recommendations(self, apps, ref=None):
        return recommendations_real.recommendations_for_apps(apps, ref)


def get_cost_provider() -> CostProvider:
    """Return the provider selected by ``settings.FINOPS_PROVIDER``."""
    if settings.FINOPS_PROVIDER == "real":
        return RealCostProvider()
    return SimulatedCostProvider()


def period_to_range(period: str, ref: date | None = None) -> tuple[date, date]:
    """Map a period token (7d/30d/90d) to a concrete [start, end] range."""
    ref = ref or date.today()
    days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)
    return ref - timedelta(days=days - 1), ref
