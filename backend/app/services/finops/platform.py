"""
Platform fee: the cost of the shared services is split in fixed parts.

An application's price is its own namespace cost plus a fixed daily fee, the
cost of the platform namespaces and of the idle capacity of that day divided
by ``settings.FINOPS_PLATFORM_APP_SLOTS``. The fee does not depend on how many
applications run, so the part nobody pays (the pool minus the fees collected)
is a figure of its own that turns negative above the planned capacity.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from app.core.config import settings

NOT_A_PROJECT = ("platform", "__idle__")
IDLE = "__idle__"
UNCOVERED_NAMESPACE = "__uncovered__"
UNCOVERED_NAME = "Plateforme non couverte"


def app_slots() -> int:
    return max(1, int(settings.FINOPS_PLATFORM_APP_SLOTS))


@dataclass
class ClusterDay:
    platform_by_namespace: dict[str, float] = field(default_factory=dict)
    idle: float = 0.0
    apps: dict[str, tuple[str | None, float]] = field(default_factory=dict)

    @property
    def platform(self) -> float:
        return sum(self.platform_by_namespace.values())

    @property
    def pool(self) -> float:
        return self.platform + self.idle

    @property
    def active(self) -> dict[str, str | None]:
        return {
            ns: project
            for ns, (project, cost) in self.apps.items()
            if cost > 0
        }

    @property
    def fee(self) -> float:
        return self.pool / app_slots()

    @property
    def recovered(self) -> float:
        return self.fee * len(self.active)

    @property
    def uncovered(self) -> float:
        return self.pool - self.recovered


def cluster_days(
    rows: Iterable[tuple[date, str, str | None, float]],
) -> dict[date, ClusterDay]:
    """(day, namespace, project, cost) of on-prem OpenCost lines, per day."""
    days: dict[date, ClusterDay] = defaultdict(ClusterDay)
    for day, namespace, project, cost in rows:
        cost = cost or 0.0
        slot = days[day]
        if project == IDLE:
            slot.idle += cost
        elif project == "platform":
            slot.platform_by_namespace[namespace] = (
                slot.platform_by_namespace.get(namespace, 0.0) + cost
            )
        else:
            previous = slot.apps.get(namespace, (project, 0.0))[1]
            slot.apps[namespace] = (project, previous + cost)
    return dict(days)


def fees_by_project(days: dict[date, ClusterDay]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for slot in days.values():
        fee = slot.fee
        for project in slot.active.values():
            totals[project or ""] += fee
    return {project: round(v, 2) for project, v in totals.items()}


def report(days: dict[date, ClusterDay], start: date, end: date) -> dict:
    chosen = sorted(d for d in days if start <= d <= end)
    by_namespace: dict[str, float] = defaultdict(float)
    for d in chosen:
        for namespace, cost in days[d].platform_by_namespace.items():
            by_namespace[namespace] += cost
    idle = sum(days[d].idle for d in chosen)
    total = sum(days[d].pool for d in chosen)
    recovered = sum(days[d].recovered for d in chosen)
    count = len(chosen)
    avg_pool = total / count if count else 0.0
    return {
        "total_eur": round(total, 2),
        "by_namespace": [
            {"namespace": ns, "eur": round(v, 2)}
            for ns, v in sorted(by_namespace.items(), key=lambda kv: -kv[1])
        ],
        "idle_eur": round(idle, 2),
        "recovered_eur": round(recovered, 2),
        "uncovered_eur": round(total - recovered, 2),
        "app_slots": app_slots(),
        "active_apps_avg": (
            round(sum(len(days[d].active) for d in chosen) / count, 2)
            if count
            else 0.0
        ),
        "fee_per_app_month_eur": round(
            avg_pool
            / app_slots()
            * calendar.monthrange(end.year, end.month)[1],
            2,
        ),
        "daily": [
            {
                "date": d.isoformat(),
                "platform_eur": round(days[d].platform, 4),
                "idle_eur": round(days[d].idle, 4),
                "recovered_eur": round(days[d].recovered, 4),
                "uncovered_eur": round(days[d].uncovered, 4),
            }
            for d in chosen
        ],
    }
