"""
Cost engine — turns application quotas + simulated usage into euro figures.

Pure and deterministic: it never touches the network, so it always works (no
cluster / GitHub dependency) and history over any date range is computed on the
fly from each app's ``created_at``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.services.finops import pricing, usage


# ── Quota / application model ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Quota:
    """Requested resources of an application (the 'allocated' capacity)."""

    replicas: int
    cpu_cores: float   # per replica
    ram_gb: float      # per replica
    storage_gb: float  # total
    network_gb: float  # baseline monthly egress allowance

    def allocated(self, resource: str) -> float:
        if resource == "cpu":
            return self.replicas * self.cpu_cores
        if resource == "ram":
            return self.replicas * self.ram_gb
        if resource == "storage":
            return self.storage_gb
        if resource == "network":
            return self.network_gb
        return 0.0


@dataclass(frozen=True)
class AppSpec:
    """Everything the engine needs about one application."""

    id: int
    name: str
    project_id: str | None
    created_at: datetime
    quota: Quota


def _stable_unit(app_id: int, key: str) -> float:
    """Stable float in [0,1) for deterministic default sizing."""
    raw = f"quota:{app_id}:{key}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") / 0xFFFFFFFF


def quota_from_deployment(deployment) -> Quota:
    """
    Derive a :class:`Quota` from a ``Deployment`` row.

    Explicit values in ``app_config`` win; otherwise deterministic defaults are
    generated from the deployment id so each app has a distinct, stable size.
    """
    cfg: dict = {}
    if getattr(deployment, "app_config", None):
        try:
            cfg = json.loads(deployment.app_config) or {}
        except (ValueError, TypeError):
            cfg = {}

    app_id = int(deployment.id)

    replicas = _coerce_int(cfg.get("replica_count") or cfg.get("replicaCount"),
                           default=1 + round(_stable_unit(app_id, "rep") * 4))
    cpu_cores = _coerce_float(_nested(cfg, "resources", "limits", "cpu"),
                              default=round(0.5 + _stable_unit(app_id, "cpu") * 3.5, 2))
    ram_gb = _coerce_float(_nested(cfg, "resources", "limits", "memory"),
                           default=round(0.5 + _stable_unit(app_id, "ram") * 7.5, 2))
    storage_gb = _coerce_float(cfg.get("storage_gb"),
                               default=round(5 + _stable_unit(app_id, "sto") * 45))
    network_gb = round(2 + _stable_unit(app_id, "net") * 18, 1)

    return Quota(
        replicas=max(1, replicas),
        cpu_cores=max(0.1, cpu_cores),
        ram_gb=max(0.1, ram_gb),
        storage_gb=max(1.0, storage_gb),
        network_gb=max(0.5, network_gb),
    )


def app_spec_from_deployment(deployment) -> AppSpec:
    created = getattr(deployment, "created_at", None) or datetime.utcnow()
    return AppSpec(
        id=int(deployment.id),
        name=deployment.name,
        project_id=deployment.project_id,
        created_at=created,
        quota=quota_from_deployment(deployment),
    )


# ── Core cost computations ────────────────────────────────────────────────────

_HOURS_PER_DAY = 24.0


def daily_cost(app: AppSpec, day: date) -> dict[str, float]:
    """Per-resource cost (EUR) of one application for a single day."""
    result: dict[str, float] = {}
    for resource in pricing.RESOURCES:
        allocated = app.quota.allocated(resource)
        mult = usage.simulated_usage_pct(app.id, resource, day)
        if usage.has_spike(app.id, resource, day):
            mult = min(1.0, mult * 1.8)
        result[resource] = pricing.resource_hours_cost(
            resource, allocated, _HOURS_PER_DAY, mult
        )
    return result


def _active_days(app: AppSpec, start: date, end: date):
    """Yield each day in [start, end] where the app already existed."""
    created_day = app.created_at.date()
    day = start
    while day <= end:
        if day >= created_day:
            yield day
        day += timedelta(days=1)


def app_cost_series(app: AppSpec, start: date, end: date) -> list[dict]:
    """Daily {date, cpu, ram, storage, network, total} points for one app."""
    points: list[dict] = []
    for day in _active_days(app, start, end):
        costs = daily_cost(app, day)
        points.append({
            "date": day.isoformat(),
            **{r: round(costs[r], 4) for r in pricing.RESOURCES},
            "total": round(sum(costs.values()), 4),
        })
    return points


def combined_series(apps: list[AppSpec], start: date, end: date) -> list[dict]:
    """Sum daily cost across many apps into one series."""
    acc: dict[str, dict[str, float]] = {}
    for app in apps:
        for point in app_cost_series(app, start, end):
            slot = acc.setdefault(
                point["date"],
                {r: 0.0 for r in pricing.RESOURCES},
            )
            for r in pricing.RESOURCES:
                slot[r] += point[r]

    out: list[dict] = []
    for day in sorted(acc):
        slot = acc[day]
        out.append({
            "date": day,
            **{r: round(slot[r], 4) for r in pricing.RESOURCES},
            "total": round(sum(slot.values()), 4),
        })
    return out


def aggregate_series(daily: list[dict], granularity: str) -> list[dict]:
    """Roll a daily series up to weekly / monthly buckets (daily = passthrough)."""
    if granularity == "daily" or not daily:
        return daily

    buckets: dict[str, dict[str, float]] = {}
    order: list[str] = []
    for point in daily:
        d = date.fromisoformat(point["date"])
        if granularity == "weekly":
            bucket_start = d - timedelta(days=d.weekday())
            key = bucket_start.isoformat()
        else:  # monthly
            key = d.replace(day=1).isoformat()
        if key not in buckets:
            buckets[key] = {r: 0.0 for r in pricing.RESOURCES}
            order.append(key)
        for r in pricing.RESOURCES:
            buckets[key][r] += point[r]

    out: list[dict] = []
    for key in order:
        slot = buckets[key]
        out.append({
            "date": key,
            **{r: round(slot[r], 4) for r in pricing.RESOURCES},
            "total": round(sum(slot.values()), 4),
        })
    return out


def month_cost(app: AppSpec, ref: date) -> float:
    """Total cost of an app for the calendar month containing ``ref``."""
    start = ref.replace(day=1)
    end = ref
    return sum(p["total"] for p in app_cost_series(app, start, end))


def month_to_date_cost(apps: list[AppSpec], ref: date | None = None) -> float:
    ref = ref or date.today()
    return round(sum(month_cost(a, ref) for a in apps), 2)


def previous_month_cost(apps: list[AppSpec], ref: date | None = None) -> float:
    ref = ref or date.today()
    first_this = ref.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    start = last_prev.replace(day=1)
    total = 0.0
    for app in apps:
        total += sum(p["total"] for p in app_cost_series(app, start, last_prev))
    return round(total, 2)


def projected_month_cost(apps: list[AppSpec], ref: date | None = None) -> float:
    """Extrapolate month-to-date spend to a full-month estimate."""
    ref = ref or date.today()
    mtd = month_to_date_cost(apps, ref)
    days_elapsed = ref.day
    days_in_month = _days_in_month(ref)
    if days_elapsed == 0:
        return 0.0
    return round(mtd / days_elapsed * days_in_month, 2)


def breakdown(apps: list[AppSpec], ref: date | None = None) -> dict[str, float]:
    """Month-to-date cost split by resource type across apps."""
    ref = ref or date.today()
    start = ref.replace(day=1)
    totals = {r: 0.0 for r in pricing.RESOURCES}
    for app in apps:
        for point in app_cost_series(app, start, ref):
            for r in pricing.RESOURCES:
                totals[r] += point[r]
    return {r: round(v, 2) for r, v in totals.items()}


def app_daily_and_monthly(app: AppSpec, ref: date | None = None) -> tuple[float, float, float]:
    """
    Return (cost_yesterday, cost_today, month_to_date) for a single app.
    The trend is derived by the caller from today vs yesterday.
    """
    ref = ref or date.today()
    today = round(sum(daily_cost(app, ref).values()), 2)
    yday = round(sum(daily_cost(app, ref - timedelta(days=1)).values()), 2)
    mtd = round(month_cost(app, ref), 2)
    return yday, today, mtd


# ── small coercion helpers ────────────────────────────────────────────────────


def _days_in_month(ref: date) -> int:
    if ref.month == 12:
        nxt = ref.replace(year=ref.year + 1, month=1, day=1)
    else:
        nxt = ref.replace(month=ref.month + 1, day=1)
    return (nxt - ref.replace(day=1)).days


def _nested(cfg: dict, *keys: str):
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _coerce_int(value, default: int) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _coerce_float(value, default: float) -> float:
    """Coerce numbers or k8s-style strings ('500m', '2Gi', '1.5') to float."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        if s.endswith("m"):  # milli-cpu
            return float(s[:-1]) / 1000.0
        if s.endswith("Gi") or s.endswith("G"):
            return float(s.rstrip("Gi").rstrip("G"))
        if s.endswith("Mi") or s.endswith("M"):
            return float(s.rstrip("Mi").rstrip("M")) / 1024.0
        return float(s)
    except (ValueError, TypeError):
        return default
