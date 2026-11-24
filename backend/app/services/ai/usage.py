"""
AI usage, cost and refusals, read from the gateway's recording rules in
VictoriaMetrics, and the platform's global AI budget.

The CMP reads only the recorded series of the metrics contract, never the
gateway's raw metrics: renaming a raw metric then only touches the rules.
"""

import asyncio
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.services.finops.ingest import fx
from app.services.finops.ingest.ai_gateway import FALLBACK_USD_PER_EUR
from app.services.metrics_isolation import VMSELECT

TOKENS = "cnp:ai_tokens_total"
COST = "cnp:ai_cost_usd_total"
REQUESTS = "cnp:ai_requests_total"
REFUSALS = "cnp:ai_refusals_total"
TOOL_CALLS = "cnp:ai_tool_calls_total"
GLOBAL_SPEND = "cnp:ai_global_spend_usd"
GLOBAL_BUDGET = "cnp:ai_global_budget_usd"
PERIODS = {"1d": "1d", "7d": "7d", "30d": "30d"}
SERIES_STEP = {"1d": 3600, "7d": 86400, "30d": 86400}
REFUSAL_REASONS = ("budget", "policy", "ratelimit", "guardrail")
WARNING_RATIO = 0.8
# Defaults offered when AI is turned on: tokens per UTC day.
APP_DAILY_TOKEN_BUDGET = 500_000
PROJECT_DAILY_TOKEN_CAP = 1_000_000
LABEL_VALUE = re.compile(r"^[A-Za-z0-9_.:-]+$")
# USD per MTok: input, output, cache read (decisions of 2026-11-22). The
# gateway's cost series is the reference; this prices a test chat live.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
}


class UsageUnavailable(RuntimeError):
    """VictoriaMetrics could not be read."""


def cost_usd(model: str, tokens_in: int, tokens_out: int, cache: int) -> float:
    price_in, price_out, price_cache = PRICES.get(
        model, PRICES["claude-sonnet-5-5"]
    )
    return (
        tokens_in * price_in + tokens_out * price_out + cache * price_cache
    ) / 1_000_000


def usd_per_eur() -> float:
    try:
        with SessionLocal() as db:
            return fx.usd_per_eur(db, date.today()) or FALLBACK_USD_PER_EUR
    except SQLAlchemyError:
        return FALLBACK_USD_PER_EUR


def _selector(project: str, azp: str | None = None) -> str:
    for value in (project, azp):
        if value is not None and not LABEL_VALUE.match(value):
            raise ValueError(f"Invalid label value {value!r}")
    labels = f'project="{project}"'
    if azp:
        labels += f',azp="{azp}"'
    return "{" + labels + "}"


async def _query(client: httpx.AsyncClient, query: str) -> list[dict]:
    try:
        response = await client.get(
            f"{VMSELECT}/api/v1/query", params={"query": query}
        )
        response.raise_for_status()
        return response.json()["data"]["result"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise UsageUnavailable(f"VictoriaMetrics: {exc}") from exc


def _kind(azp: str, project: str) -> tuple[str, str]:
    for kind in ("agent", "app"):
        prefix = f"{kind}-{project}-"
        if azp.startswith(prefix):
            return kind, azp[len(prefix) :]
    return "other", azp


def empty_row(azp: str, project: str) -> dict[str, Any]:
    kind, name = _kind(azp, project)
    return {
        "azp": azp,
        "kind": kind,
        "name": name,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cache": 0,
        "requests": 0,
        "errors": 0,
        "refused": {reason: 0 for reason in REFUSAL_REASONS},
        "refused_total": 0,
        "tool_calls": 0,
        "budget": None,
        "cost_usd": 0.0,
        "cost_eur": 0.0,
    }


async def usage(
    project: str, period: str, azp: str | None = None
) -> list[dict[str, Any]]:
    """
    One row per caller (``azp``) of the project over *period*, "1d" being
    the UTC calendar day so far (what the daily quota counts).

    Raises:
        UsageUnavailable: VictoriaMetrics could not be read.
    """
    window = (
        f"{utc_day(datetime.now(timezone.utc))[0]}s"
        if period == "1d"
        else PERIODS[period]
    )
    sel = _selector(project, azp)
    queries = {
        "tokens": f"sum by (azp, direction) (increase({TOKENS}{sel}[{window}]))",
        "cost": f"sum by (azp) (increase({COST}{sel}[{window}]))",
        "requests": f"sum by (azp, status) (increase({REQUESTS}{sel}[{window}]))",
        "refusals": f"sum by (azp, reason) (increase({REFUSALS}{sel}[{window}]))",
        "tools": f"sum by (azp) (increase({TOOL_CALLS}{sel}[{window}]))",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        results = dict(
            zip(
                queries,
                await asyncio.gather(
                    *(_query(client, q) for q in queries.values())
                ),
            )
        )
    rows: dict[str, dict] = defaultdict(dict)

    def row(sample: dict) -> dict:
        key = sample["metric"].get("azp", "unknown")
        if not rows[key]:
            rows[key] = empty_row(key, project)
        return rows[key]

    def value(sample: dict) -> float:
        return float(sample["value"][1])

    for sample in results["tokens"]:
        direction = sample["metric"].get("direction", "in")
        field = {"out": "tokens_out", "cache": "tokens_cache"}.get(
            direction, "tokens_in"
        )
        row(sample)[field] += round(value(sample))
    for sample in results["cost"]:
        row(sample)["cost_usd"] += value(sample)
    for sample in results["requests"]:
        item = row(sample)
        count = round(value(sample))
        item["requests"] += count
        if str(sample["metric"].get("status", "")).startswith("5"):
            item["errors"] += count
    for sample in results["refusals"]:
        item = row(sample)
        reason = sample["metric"].get("reason", "policy")
        if reason in item["refused"]:
            item["refused"][reason] += round(value(sample))
        item["refused_total"] += round(value(sample))
    for sample in results["tools"]:
        row(sample)["tool_calls"] += round(value(sample))
    rate = await asyncio.to_thread(usd_per_eur)
    for item in rows.values():
        item["cost_usd"] = round(item["cost_usd"], 6)
        item["cost_eur"] = round(item["cost_usd"] / rate, 6)
    return sorted(rows.values(), key=lambda r: -r["cost_usd"])


def utc_day(now: datetime) -> tuple[int, datetime]:
    """
    Seconds elapsed since 00:00 UTC and the next 00:00 UTC: the token quota
    resets then (the gateway's rate limit counts per UTC day), so usage is
    read over the calendar day, not a rolling 24 h.
    """
    midnight = now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    elapsed = max(1, int((now - midnight).total_seconds()))
    return elapsed, midnight + timedelta(days=1)


async def today_by_azp(
    project: str | None = None, now: datetime | None = None
) -> dict[str, dict]:
    """
    Tokens (cache reads left out) and cost since 00:00 UTC per caller, of
    one project or of all: one MetricsQL query for every agent of a list.

    Raises:
        UsageUnavailable: VictoriaMetrics could not be read.
    """
    labels = f'__name__=~"{TOKENS}|{COST}",direction!="cache"'
    if project is not None:
        labels += "," + _selector(project)[1:-1]
    elapsed, _ = utc_day(now or datetime.now(timezone.utc))
    query = (
        f"sum by (azp, __name__) "
        f"(increase({{{labels}}}[{elapsed}s]) keep_metric_names)"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        samples = await _query(client, query)
    found: dict[str, dict] = defaultdict(
        lambda: {"tokens": 0, "cost_usd": 0.0, "cost_eur": 0.0}
    )
    for sample in samples:
        item = found[sample["metric"].get("azp", "unknown")]
        value = float(sample["value"][1])
        if sample["metric"].get("__name__") == COST:
            item["cost_usd"] += value
        else:
            item["tokens"] += round(value)
    rate = await asyncio.to_thread(usd_per_eur)
    for item in found.values():
        item["cost_usd"] = round(item["cost_usd"], 6)
        item["cost_eur"] = round(item["cost_usd"] / rate, 6)
    return dict(found)


def totals(items: list[dict]) -> dict[str, Any]:
    keys = ("tokens_in", "tokens_out", "tokens_cache", "requests")
    total: dict[str, Any] = {k: sum(i[k] for i in items) for k in keys}
    total["refused_total"] = sum(i["refused_total"] for i in items)
    total["cost_usd"] = round(sum(i["cost_usd"] for i in items), 6)
    total["cost_eur"] = round(sum(i["cost_eur"] for i in items), 6)
    return total


async def series(project: str, azp: str, period: str) -> list[dict]:
    """Tokens and cost per step (hour over a day, else day) of one caller."""
    step = SERIES_STEP[period]
    end = datetime.now(timezone.utc)
    start = end - timedelta(
        seconds=step * (24 if period == "1d" else int(period[:-1]))
    )
    sel = _selector(project, azp)
    span = "1h" if step == 3600 else "1d"
    out: dict[float, dict] = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for field, metric in (("tokens", TOKENS), ("cost_usd", COST)):
            try:
                response = await client.get(
                    f"{VMSELECT}/api/v1/query_range",
                    params={
                        "query": f"sum(increase({metric}{sel}[{span}]))",
                        "start": int(start.timestamp()),
                        "end": int(end.timestamp()),
                        "step": step,
                    },
                )
                response.raise_for_status()
                result = response.json()["data"]["result"]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise UsageUnavailable(f"VictoriaMetrics: {exc}") from exc
            for stamp, raw in result[0]["values"] if result else []:
                point = out.setdefault(
                    stamp,
                    {
                        "t": datetime.fromtimestamp(
                            stamp, timezone.utc
                        ).isoformat(),
                        "tokens": 0,
                        "cost_usd": 0.0,
                    },
                )
                point[field] = (
                    round(float(raw))
                    if field == "tokens"
                    else round(float(raw), 6)
                )
    return [out[k] for k in sorted(out)]


async def budget() -> dict[str, Any]:
    """The platform's cumulative AI spend against its cap."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        spend, cap = await asyncio.gather(
            _query(client, f"max({GLOBAL_SPEND})"),
            _query(client, f"max({GLOBAL_BUDGET})"),
        )
    spend_usd = float(spend[0]["value"][1]) if spend else 0.0
    budget_usd = float(cap[0]["value"][1]) if cap else None
    ratio = spend_usd / budget_usd if budget_usd else None
    status = "ok"
    if ratio is not None and ratio >= 1:
        status = "exhausted"
    elif ratio is not None and ratio >= WARNING_RATIO:
        status = "warning"
    return {
        "spend_usd": round(spend_usd, 6),
        "budget_usd": budget_usd,
        "ratio": round(ratio, 4) if ratio is not None else None,
        "status": status,
    }
