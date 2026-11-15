"""
Cost per 1,000 requests and waste of an app (D19), from the FinOps code:
the cost provider's daily series (OpenCost when real), the requests Envoy
counted over the same days, and the measured usage the recommendations read
(recommendations_real) against what the app requests.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool

from app.models.deployment import Deployment
from app.services.finops import recommendations_real
from app.services.finops.provider import get_cost_provider
from app.services.observability import signals, stores

logger = logging.getLogger(__name__)

DAYS = 30


def _cost(
    deployment: Deployment, today: date
) -> tuple[float, list[dict], list[dict]]:
    provider = get_cost_provider()
    specs = provider.specs([deployment])
    daily = provider.timeline(
        specs, today - timedelta(days=DAYS), today, "daily"
    )
    return (
        sum(float(p.get("total") or 0.0) for p in daily),
        [
            {"date": str(p["date"]), "total_eur": float(p.get("total") or 0.0)}
            for p in daily
        ],
        [
            {
                "title": r.title,
                "rec_type": r.rec_type,
                "monthly_saving_eur": r.monthly_saving_eur,
                "justification": r.justification,
            }
            for r in provider.recommendations(specs)
        ],
    )


def _usage(scope: stores.Scope) -> dict[str, Any] | None:
    try:
        return recommendations_real.measured_usage(
            scope.namespace, scope.cloud
        )
    except (httpx.HTTPError, KeyError, ValueError, OSError) as exc:
        logger.warning("Usage of %s not measured: %s", scope.namespace, exc)
        return None


def _idle(request: float | None, used: float | None) -> float | None:
    if not request or used is None:
        return None
    return max(0.0, 1 - used / request)


async def app_costs(
    deployment: Deployment, scope: stores.Scope, now: datetime
) -> dict[str, Any]:
    total, daily, recommendations = await run_in_threadpool(
        _cost, deployment, now.date()
    )
    requests = None
    metrics = stores.capabilities()["metrics"]
    if metrics:
        requests = await signals.requests_over(scope, DAYS, now)
    usage = await run_in_threadpool(_usage, scope)
    return {
        "days": DAYS,
        "cost_eur": round(total, 4),
        "daily": daily,
        "requests": requests,
        "metrics_available": metrics,
        "cost_per_1k_requests_eur": (
            round(total / (requests / 1000), 6) if requests else None
        ),
        "usage": usage,
        "cpu_idle_ratio": _idle(
            (usage or {}).get("cpu_request_cores"),
            (usage or {}).get("cpu_p95_cores"),
        ),
        "ram_idle_ratio": _idle(
            (usage or {}).get("ram_request_gib"),
            (usage or {}).get("ram_p99_gib"),
        ),
        "recommendations": recommendations,
        "monthly_waste_eur": round(
            sum(r["monthly_saving_eur"] for r in recommendations), 2
        ),
    }
