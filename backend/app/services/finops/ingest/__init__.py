"""
FinOps ingester: normalises every cost source into FOCUS lines in
``cost_line_items``. Runs in the backend like the other collectors, replays the
last days on every pass (sources revise them) and never fails the loop on one
source's error.
"""

import asyncio
import logging
from datetime import date, timedelta

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.activity_sources import namespace_projects
from app.services.finops.ingest import (
    ai_gateway,
    carbon,
    fx,
    inputs,
    opencost,
    saas,
    savings,
)
from app.services.finops.ingest.store import upsert
from app.services.kube_client import KubeUnavailableError, in_cluster

logger = logging.getLogger(__name__)


def days_to_ingest(today: date) -> list[date]:
    since = date.fromisoformat(settings.FINOPS_INGEST_SINCE)
    first = max(since, today - timedelta(days=settings.FINOPS_REPLAY_DAYS))
    return [first + timedelta(days=i) for i in range((today - first).days)]


def is_final(day: date, today: date) -> bool:
    """The last pass over a day: later passes no longer touch it."""
    return (today - day).days >= settings.FINOPS_REPLAY_DAYS


def ingest_once(today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    days = days_to_ingest(today)
    written = {"opencost": 0, "ai_gateway": 0, "saas": 0, "savings": 0}
    if not days:
        return written

    try:
        ns_projects = namespace_projects() if in_cluster() else {}
    except KubeUnavailableError as exc:
        logger.warning("FinOps: project namespaces unknown: %s", exc)
        ns_projects = {}
    tco = inputs.onprem_tco()
    prices, local_backends = inputs.ai_prices()

    with SessionLocal() as db:
        fx.refresh(db)
        try:
            carbon.refresh(db, days[0])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("FinOps: carbon intensity not refreshed: %s", exc)
        for day in days:
            final = is_final(day, today)
            g_per_kwh = carbon.intensity_for(db, day)
            try:
                allocations = opencost.fetch_day(day)
                lines = opencost.to_lines(
                    day, allocations, ns_projects, tco, final
                )
                carbon.apply_to_lines(lines, g_per_kwh)
                written["opencost"] += upsert(db, lines)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "FinOps: OpenCost %s not ingested: %s", day, exc
                )
            try:
                usage = ai_gateway.fetch_day(day)
                lines = ai_gateway.to_lines(
                    day,
                    usage,
                    prices,
                    local_backends,
                    tco,
                    fx.usd_per_eur(db, day),
                    final,
                )
                carbon.apply_to_lines(lines, g_per_kwh)
                written["ai_gateway"] += upsert(db, lines)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "FinOps: AI usage %s not ingested: %s", day, exc
                )
            try:
                written["savings"] += savings.store(
                    db,
                    savings.to_entries(
                        day,
                        savings.fetch_day(day, tco),
                        ns_projects,
                        tco,
                        g_per_kwh,
                    ),
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("FinOps: savings %s not computed: %s", day, exc)
        months = sorted({d.replace(day=1) for d in days})
        subscriptions = inputs.saas_subscriptions()
        for month in months:
            written["saas"] += upsert(db, saas.to_lines(month, subscriptions))
    return written


async def finops_ingest_loop() -> None:
    logger.info("FinOps ingester started")
    while True:
        try:
            written = await run_in_threadpool(ingest_once)
            logger.info("FinOps ingester wrote %s", written)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("FinOps ingester error: %s", exc, exc_info=True)
        await asyncio.sleep(settings.FINOPS_INGEST_INTERVAL_SECONDS)
