"""
Daily AI usage from the gateway's token metrics, valued at the provider's
public price (D-F8). Models served by the homelab GPU server are valued from
its energy cost, split over the tokens it served that day.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import httpx

from app.core.config import settings
from app.services.finops.ingest.inputs import ModelPrice, OnPremTCO, price_for
from app.services.finops.ingest.store import CostLine
from app.services.metrics_isolation import VMSELECT

logger = logging.getLogger(__name__)

SOURCE = "ai_gateway"
MODEL = "gen_ai_request_model"
TOKEN_TYPE = "gen_ai_token_type"
BACKEND = "gen_ai_provider_name"
AGENT = "azp"
PROJECT = "project"
# Used only when no ECB rate was ever stored; the line is marked estimate.
FALLBACK_USD_PER_EUR = 1.10


@dataclass(frozen=True)
class Usage:
    project: str
    agent: str
    model: str
    backend: str
    input_tokens: float
    output_tokens: float

    @property
    def tokens(self) -> float:
        return self.input_tokens + self.output_tokens


def fetch_day(day: date) -> list[Usage]:
    end = datetime.combine(day, time.min) + timedelta(days=1)
    by = ",".join((PROJECT, AGENT, MODEL, BACKEND, TOKEN_TYPE))
    query = (
        f"sum by ({by}) " f"(increase({settings.FINOPS_AI_TOKEN_METRIC}[1d]))"
    )
    response = httpx.get(
        f"{VMSELECT}/api/v1/query",
        params={"query": query, "time": int(end.timestamp())},
        timeout=30.0,
    )
    response.raise_for_status()
    return parse_vector(response.json()["data"]["result"])


def parse_vector(result: list[dict]) -> list[Usage]:
    totals: dict[tuple, dict[str, float]] = defaultdict(
        lambda: {"input": 0.0, "output": 0.0}
    )
    for sample in result:
        labels = sample["metric"]
        key = (
            labels.get(PROJECT, "unknown"),
            labels.get(AGENT, "unknown"),
            labels.get(MODEL, "unknown"),
            labels.get(BACKEND, "unknown"),
        )
        kind = labels.get(TOKEN_TYPE, "input")
        totals[key]["output" if kind == "output" else "input"] += float(
            sample["value"][1]
        )
    return [
        Usage(*key, input_tokens=t["input"], output_tokens=t["output"])
        for key, t in totals.items()
        if t["input"] + t["output"] > 0
    ]


def to_lines(
    day: date,
    usage: list[Usage],
    prices: list[ModelPrice],
    local_backends: set[str],
    tco: OnPremTCO,
    usd_per_eur: float | None,
    is_final: bool,
) -> list[CostLine]:
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    rate = usd_per_eur or FALLBACK_USD_PER_EUR
    local_tokens = sum(u.tokens for u in usage if u.backend in local_backends)
    gpu_day_eur = tco.gpu_server_watts * 24 / 1000 * tco.eur_per_kwh

    lines = []
    for u in usage:
        common = dict(
            x_source=SOURCE,
            charge_period_start=start,
            charge_period_end=end,
            resource_id=f"{u.project}/{u.agent}/{u.model}/{u.backend}",
            resource_name=u.model,
            consumed_quantity=u.tokens,
            consumed_unit="Tokens",
            project=u.project,
            app=u.agent,
            service_category="AI and Machine Learning",
            x_allocation_method="direct",
            x_is_final=is_final,
            extra_tags={"cnp_agent": u.agent, "model": u.model},
        )
        if u.backend in local_backends:
            share = (
                gpu_day_eur * u.tokens / local_tokens if local_tokens else 0
            )
            lines.append(
                CostLine(
                    provider_name="CNP",
                    service_name="Serveur GPU",
                    x_cost_eur=share,
                    billed_cost=share,
                    list_cost=share,
                    effective_cost=share,
                    x_pricing_basis="tco",
                    x_energy_kwh=(
                        share / tco.eur_per_kwh if tco.eur_per_kwh else None
                    ),
                    x_is_estimate=tco.gpu_has_mock,
                    **common,
                )
            )
            continue
        price = price_for(prices, u.model, day)
        if price is None:
            logger.warning("FinOps: no public price for model %s", u.model)
            lines.append(
                CostLine(
                    provider_name=u.backend,
                    service_name=u.model,
                    x_cost_eur=0.0,
                    billed_cost=0.0,
                    list_cost=0.0,
                    effective_cost=0.0,
                    x_pricing_basis="public_price",
                    x_is_estimate=True,
                    **common,
                )
            )
            continue
        list_usd = (
            u.input_tokens * price.input_usd_per_mtok
            + u.output_tokens * price.output_usd_per_mtok
        ) / 1_000_000
        lines.append(
            CostLine(
                provider_name=price.provider,
                service_name=price.service,
                x_cost_eur=list_usd / rate,
                # Free tier and prepaid credits: nothing is invoiced per call.
                billed_cost=0.0,
                list_cost=list_usd,
                effective_cost=0.0,
                billing_currency="USD",
                x_fx_rate=rate,
                x_pricing_basis="public_price",
                x_is_estimate=usd_per_eur is None,
                **common,
            )
        )
    return lines
