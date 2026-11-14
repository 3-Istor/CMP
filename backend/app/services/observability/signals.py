"""
Golden signals and SLO status of an app, from the Envoy series of its routes
and the series its SLO rules record (D14, D16).

Envoy only exports a route's series after its first request since the proxy
started (deferred stats): no series is "no traffic yet", not an error.
"""

from datetime import datetime, timedelta
from typing import Any

from app.services.observability import slo, stores
from app.services.observability.stores import Scope

RANGES = {
    "1h": 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
}
POINTS = 120
RATE = "5m"
ROUTE_LABEL = "envoy_cluster_name"


def step_of(range_name: str) -> int:
    return max(60, RANGES[range_name] // POINTS)


def route_name(cluster_name: str, namespace: str) -> str:
    """httproute/<ns>/<route>/rule/0 shown as <route>/rule/0."""
    prefix = f"httproute/{namespace}/"
    return (
        cluster_name[len(prefix) :]
        if cluster_name.startswith(prefix)
        else cluster_name
    )


def _quantile(q: float, sel: str, by: str, window: str) -> str:
    return (
        f"histogram_quantile({q}, sum by ({by}) "
        f"(rate(envoy_cluster_upstream_rq_time_bucket{{{sel}}}[{window}])))"
    )


def queries(
    namespace: str, window: str = RATE, by: str = ""
) -> dict[str, str]:
    """
    The four signals, summed over the app's routes or per *by* label.
    Latency is Envoy's upstream time, in milliseconds.
    """
    sel = slo.envoy_selector(namespace)
    group = f" by ({by})" if by else ""
    le = f"{by}, le" if by else "le"
    return {
        "rate": (
            f"sum{group} (rate(envoy_cluster_upstream_rq_total{{{sel}}}"
            f"[{window}]))"
        ),
        "errors": (
            f"sum{group} (rate(envoy_cluster_upstream_rq_xx{{{sel}, "
            f'envoy_response_code_class="5"}}[{window}]))'
        ),
        "p50": _quantile(0.5, sel, le, window),
        "p95": _quantile(0.95, sel, le, window),
    }


def _points(rows: list[dict]) -> list[tuple[datetime, float]]:
    return rows[0]["points"] if rows else []


def error_percent(
    total: list[tuple[datetime, float]], errors: list[tuple[datetime, float]]
) -> list[tuple[datetime, float]]:
    failed = dict(errors)
    return [
        (when, 100 * failed.get(when, 0.0) / value)
        for when, value in total
        if value > 0
    ]


async def golden_signals(
    scope: Scope, range_name: str, now: datetime
) -> dict[str, Any]:
    seconds = RANGES[range_name]
    step = step_of(range_name)
    start = now - timedelta(seconds=seconds)
    totals = queries(scope.namespace)
    rate = _points(
        await stores.series(scope, totals["rate"], start, now, step)
    )
    if not rate:
        return {
            "range": range_name,
            "step_seconds": step,
            "no_traffic": True,
            "rate": [],
            "error_percent": [],
            "p50_ms": [],
            "p95_ms": [],
            "routes": [],
        }
    errors = _points(
        await stores.series(scope, totals["errors"], start, now, step)
    )
    p50 = _points(await stores.series(scope, totals["p50"], start, now, step))
    p95 = _points(await stores.series(scope, totals["p95"], start, now, step))
    return {
        "range": range_name,
        "step_seconds": step,
        "no_traffic": False,
        "rate": rate,
        "error_percent": error_percent(rate, errors),
        "p50_ms": p50,
        "p95_ms": p95,
        "routes": await routes(scope, range_name, now),
    }


async def routes(
    scope: Scope, range_name: str, now: datetime
) -> list[dict[str, Any]]:
    """Per route over the whole range: rate, 5xx share, p50 and p95."""
    per_route = queries(scope.namespace, range_name, ROUTE_LABEL)
    found: dict[str, dict[str, Any]] = {}
    for signal, query in per_route.items():
        for row in await stores.instant(scope, query, now):
            name = row["metric"].get(ROUTE_LABEL, "")
            found.setdefault(name, {})[signal] = row["value"]
    rows = []
    for name, values in found.items():
        rate = values.get("rate", 0.0)
        rows.append(
            {
                "route": route_name(name, scope.namespace),
                "requests_per_second": rate,
                "error_percent": (
                    100 * values.get("errors", 0.0) / rate if rate else 0.0
                ),
                "p50_ms": values.get("p50"),
                "p95_ms": values.get("p95"),
            }
        )
    rows.sort(key=lambda r: r["requests_per_second"], reverse=True)
    return rows


async def requests_over(
    scope: Scope, days: int, now: datetime
) -> float | None:
    """Requests served over the last *days*, None when Envoy has no series."""
    sel = slo.envoy_selector(scope.namespace)
    rows = await stores.instant(
        scope,
        f"sum(increase(envoy_cluster_upstream_rq_total{{{sel}}}[{days}d]))",
        now,
    )
    return rows[0]["value"] if rows else None


# ── SLO status ───────────────────────────────────────────────────────────────


def _slo_selector(scope: Scope) -> str:
    return (
        f'cnp_project="{scope.project}", cnp_app="{scope.app}", '
        f'namespace="{scope.namespace}"'
    )


def _by_kind(rows: list[dict]) -> dict[str, float]:
    return {r["metric"].get("cnp_slo", ""): r["value"] for r in rows}


async def slo_status(
    scope: Scope, period_days: int, now: datetime, range_name: str
) -> dict[str, Any]:
    """
    Per objective: what is left of the period's error budget, and how fast
    it burns over 1 h and 6 h (1 = exactly on budget).
    """
    sel = _slo_selector(scope)
    period = f"{period_days}d"
    remaining = _by_kind(
        await stores.instant(
            scope, f"slo:period_error_budget_remaining:ratio{{{sel}}}", now
        )
    )
    budget = _by_kind(
        await stores.instant(scope, f"slo:error_budget:ratio{{{sel}}}", now)
    )
    burns = {
        window: _by_kind(
            await stores.instant(
                scope, f"slo:sli_error:ratio_rate{window}{{{sel}}}", now
            )
        )
        for window in ("1h", "6h")
    }
    sli = _by_kind(
        await stores.instant(
            scope, f"slo:sli_error:ratio_rate{period}{{{sel}}}", now
        )
    )
    start = now - timedelta(seconds=RANGES[range_name])
    trend = {
        r["metric"].get("cnp_slo", ""): r["points"]
        for r in await stores.series(
            scope,
            f"slo:period_error_budget_remaining:ratio{{{sel}}}",
            start,
            now,
            step_of(range_name),
        )
    }
    objectives = []
    for kind in slo.KINDS:
        allowed = budget.get(kind)
        objectives.append(
            {
                "kind": kind,
                "budget_remaining": remaining.get(kind),
                "error_ratio": sli.get(kind),
                "burn_rate_1h": _burn(burns["1h"].get(kind), allowed),
                "burn_rate_6h": _burn(burns["6h"].get(kind), allowed),
                "trend": trend.get(kind, []),
            }
        )
    return {"objectives": objectives}


def _burn(ratio: float | None, budget: float | None) -> float | None:
    if ratio is None or not budget:
        return None
    return ratio / budget
