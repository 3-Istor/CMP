"""
End-of-month cost forecast from a short daily history.

The cost store holds days, not years, so no seasonal model can be fitted.
Each remaining day of the month is predicted by the mean of the same weekday
over the last complete days (up to 28), or by the plain mean when fewer than
two weeks are known. The interval is a Student-t prediction interval built
from the in-sample residual spread; it also counts the error of the means
themselves, which dominates when each weekday has only a few samples.
"""

from __future__ import annotations

import calendar
import math
from collections import defaultdict
from datetime import date, timedelta

HISTORY_DAYS = 28
MIN_HISTORY_DAYS = 7
WEEKDAY_MIN_HISTORY_DAYS = 14
CONFIDENCE = 0.8
# Below MIN_HISTORY_DAYS a handful of near-identical days would give a
# misleadingly tight band: daily spread is floored at this share of the mean.
LOW_HISTORY_REL_SD = 0.25

# One-sided 90 % quantile of Student's t (two-sided 80 % interval).
_T90 = {
    1: 3.078,
    2: 1.886,
    3: 1.638,
    4: 1.533,
    5: 1.476,
    6: 1.440,
    7: 1.415,
    8: 1.397,
    9: 1.383,
    10: 1.372,
    12: 1.356,
    15: 1.341,
    20: 1.325,
    25: 1.316,
    30: 1.310,
}
_Z90 = 1.2816


def t_quantile(dof: int) -> float:
    """80 % two-sided Student-t multiplier, conservative between table rows."""
    if dof > 30:
        return _Z90
    dof = max(dof, 1)
    return _T90[max(k for k in _T90 if k <= dof)]


def daily_totals(points: list[dict]) -> dict[date, float]:
    """Provider timeline points (daily granularity) to {day: total EUR}."""
    return {date.fromisoformat(p["date"]): p["total"] for p in points}


def _month_end(ref: date) -> date:
    return ref.replace(day=calendar.monthrange(ref.year, ref.month)[1])


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _overrun_date(
    cumulative: list[tuple[date, float]], budget_eur: float | None
) -> date | None:
    if not budget_eur or budget_eur <= 0:
        return None
    return next((d for d, total in cumulative if total > budget_eur), None)


def forecast_month(
    daily: dict[date, float],
    ref: date,
    budget_eur: float | None = None,
) -> dict:
    """
    Forecast the month of ``ref`` from ``daily`` costs.

    Days on or after ``ref`` are partial (today) or absent, so the history
    ends on the last day before ``ref`` that has data; every later day of the
    month is forecast. Days without data inside the history are ingest gaps,
    not free days, and are left out rather than counted as zero.
    """
    first, month_end = ref.replace(day=1), _month_end(ref)
    complete = {d: v for d, v in daily.items() if d < ref}
    last_day = max(complete) if complete else None

    actual_days = sorted(d for d in complete if d >= first)
    actual = sum(complete[d] for d in actual_days)
    remaining = _days(
        max(first, last_day + timedelta(days=1)) if last_day else first,
        month_end,
    )
    history = (
        {
            d: v
            for d, v in complete.items()
            if d > last_day - timedelta(days=HISTORY_DAYS)
        }
        if last_day
        else {}
    )
    n_hist = len(history)
    cumulative: list[tuple[date, float]] = []
    running = 0.0
    for d in actual_days:
        running += complete[d]
        cumulative.append((d, running))

    if n_hist == 0:
        return {
            "projected_eur": round(actual, 2),
            "low_eur": round(actual, 2),
            "high_eur": round(actual, 2),
            "actual_eur": round(actual, 2),
            "method": "none",
            "days_of_history": 0,
            "low_history": True,
            "confidence": CONFIDENCE,
            "budget_eur": budget_eur,
            "overrun_date": _overrun_date(cumulative, budget_eur),
            "daily": [],
        }

    overall = sum(history.values()) / n_hist
    use_weekday = n_hist >= WEEKDAY_MIN_HISTORY_DAYS

    def key(day: date) -> object:
        return day.weekday() if use_weekday else "all"

    groups: dict[object, list[float]] = defaultdict(list)
    for d, v in history.items():
        groups[key(d)].append(v)
    means = {g: sum(vs) / len(vs) for g, vs in groups.items()}
    ssr = sum((v - means[key(d)]) ** 2 for d, v in history.items())
    dof = n_hist - len(means)
    sigma = math.sqrt(ssr / dof) if dof > 0 else 0.0
    # A weekday never seen (ingest gaps) falls back to the plain mean.
    groups.setdefault("all", list(history.values()))
    means.setdefault("all", overall)

    def group_of(day: date) -> object:
        return key(day) if key(day) in means else "all"

    low_history = n_hist < MIN_HISTORY_DAYS
    if low_history:
        sigma = max(sigma, LOW_HISTORY_REL_SD * overall)
    t = t_quantile(dof)

    per_group: dict[object, int] = defaultdict(int)
    series = []
    forecast_total = 0.0
    for d in remaining:
        g = group_of(d)
        per_group[g] += 1
        point = means[g]
        forecast_total += point
        half = t * sigma * math.sqrt(1 + 1 / len(groups[g]))
        series.append(
            {
                "date": d.isoformat(),
                "eur": round(point, 4),
                "low_eur": round(max(0.0, point - half), 4),
                "high_eur": round(point + half, 4),
            }
        )
        running += point
        cumulative.append((d, running))

    variance = sigma**2 * (
        len(remaining)
        + sum(c**2 / len(groups[g]) for g, c in per_group.items())
    )
    half_total = t * math.sqrt(variance)
    projected = actual + forecast_total
    return {
        "projected_eur": round(projected, 2),
        "low_eur": round(max(actual, projected - half_total), 2),
        "high_eur": round(projected + half_total, 2),
        "actual_eur": round(actual, 2),
        "method": "weekday_mean" if use_weekday else "mean",
        "days_of_history": n_hist,
        "low_history": low_history,
        "confidence": CONFIDENCE,
        "budget_eur": budget_eur,
        "overrun_date": _overrun_date(cumulative, budget_eur),
        "daily": series,
    }


def forecast_for_apps(
    provider, apps: list, ref: date | None = None, budget_eur=None
) -> dict:
    """Forecast the current month of ``apps`` from the provider's timeline."""
    ref = ref or date.today()
    # A week of slack so a lagging ingest still leaves 28 days of history.
    start = min(ref.replace(day=1), ref - timedelta(days=HISTORY_DAYS + 7))
    points = provider.timeline(apps, start, ref - timedelta(days=1), "daily")
    return forecast_month(daily_totals(points), ref, budget_eur)
