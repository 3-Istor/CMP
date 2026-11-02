"""
FinOps ingester: normalises every cost source into FOCUS lines in
``cost_line_items``. Runs in the backend like the other collectors, replays the
last days on every pass (sources revise them) and never fails the loop on one
source's error.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.finops import CostLineItem
from app.services.activity_sources import namespace_projects
from app.services.finops.ingest import (
    ai_gateway,
    aws_focus,
    carbon,
    fx,
    gcp_focus,
    inputs,
    opencost,
    saas,
    savings,
    split,
)
from app.services.finops.ingest.store import upsert
from app.services.kube_client import KubeUnavailableError, in_cluster

logger = logging.getLogger(__name__)


def days_to_ingest(
    today: date, ingested: set[date] | None = None
) -> list[date]:
    """
    Today (provisional, refreshed every pass), the replay window, and any
    earlier day since FINOPS_INGEST_SINCE that was never ingested: a first
    run backfills what VictoriaMetrics still holds.
    """
    since = date.fromisoformat(settings.FINOPS_INGEST_SINCE)
    replay_from = today - timedelta(days=settings.FINOPS_REPLAY_DAYS)
    ingested = ingested or set()
    return [
        since + timedelta(days=i)
        for i in range((today - since).days + 1)
        if since + timedelta(days=i) >= replay_from
        or since + timedelta(days=i) not in ingested
    ]


def ingested_days(db) -> set[date]:
    rows = db.scalars(
        select(CostLineItem.charge_period_start)
        .where(CostLineItem.x_source == opencost.SOURCE)
        .distinct()
    )
    return {row.date() for row in rows}


def is_final(day: date, today: date) -> bool:
    """The last pass over a day: later passes no longer touch it."""
    return (today - day).days >= settings.FINOPS_REPLAY_DAYS


def ingest_once(today: date | None = None) -> dict[str, int]:
    today = today or datetime.now(timezone.utc).date()
    with SessionLocal() as db:
        days = days_to_ingest(today, ingested_days(db))
    written = {
        "opencost": 0,
        "ai_gateway": 0,
        "saas": 0,
        "savings": 0,
        "aws_focus": 0,
        "gcp_focus": 0,
        "aws_split": 0,
        "gcp_split": 0,
    }
    if not days:
        return written

    try:
        ns_projects = namespace_projects() if in_cluster() else {}
    except KubeUnavailableError as exc:
        logger.warning("FinOps: project namespaces unknown: %s", exc)
        ns_projects = {}
    tco = inputs.onprem_tco()
    prices, local_backends = inputs.ai_prices()
    try:
        offhours_targets = savings.targets()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("FinOps: offhours targets unknown: %s", exc)
        offhours_targets = {}

    with SessionLocal() as db:
        fx.refresh(db)
        try:
            carbon.refresh(db, days[0])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("FinOps: carbon intensity not refreshed: %s", exc)
        for day in days:
            final = is_final(day, today)
            g_per_kwh = carbon.intensity_for(db, day)
            for cloud, url in opencost.clusters().items():
                try:
                    allocations = opencost.fetch_day(day, url)
                    lines = opencost.to_lines(
                        day, allocations, ns_projects, tco, final, cloud
                    )
                    carbon.apply_to_lines(lines, g_per_kwh)
                    written["opencost"] += upsert(db, lines)
                except (
                    Exception
                ) as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "FinOps: OpenCost %s %s not ingested: %s",
                        cloud,
                        day,
                        exc,
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
            rates = inputs.cluster_rates(fx.usd_per_eur(db, day))
            for cluster, by_namespace in offhours_targets.items():
                if cluster not in rates:
                    continue
                try:
                    written["savings"] += savings.store(
                        db,
                        savings.to_entries(
                            day,
                            savings.fetch_day(day, cluster, by_namespace),
                            ns_projects,
                            rates[cluster],
                            tco,
                            g_per_kwh,
                            cluster,
                        ),
                    )
                except (
                    Exception
                ) as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "FinOps: savings %s %s not computed: %s",
                        cluster,
                        day,
                        exc,
                    )
        months = sorted({d.replace(day=1) for d in days})
        subscriptions = inputs.saas_subscriptions()
        for month in months:
            written["saas"] += upsert(db, saas.to_lines(month, subscriptions))
        _ingest_cloud_billing(db, today, days, written)
    return written


def _ingest_cloud_billing(
    db, today: date, window: list[date], written: dict[str, int]
) -> None:
    """AWS and GCP invoices, then the split of their cluster nodes. A source
    whose settings are empty is skipped silently."""
    since = date.fromisoformat(settings.FINOPS_INGEST_SINCE)

    def rate_for(currency: str, day: date) -> float | None:
        return fx.rate_per_eur(db, currency, day)

    sources = (
        ("aws", aws_focus, aws_focus.enabled()),
        ("gcp", gcp_focus, gcp_focus.enabled()),
    )
    for cloud, module, is_enabled in sources:
        if not is_enabled:
            continue
        try:
            lines = module.fetch_lines(today, window, since, rate_for)
            written[f"{cloud}_focus"] += upsert(db, lines)
            written[f"{cloud}_split"] += split.split_cloud(
                db, cloud, {line.charge_period_start.date() for line in lines}
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            db.rollback()
            logger.warning("FinOps: %s billing not ingested: %s", cloud, exc)


async def finops_ingest_loop() -> None:
    logger.info("FinOps ingester started")
    while True:
        try:
            written = await run_in_threadpool(ingest_once)
            logger.info("FinOps ingester wrote %s", written)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("FinOps ingester error: %s", exc, exc_info=True)
        await asyncio.sleep(settings.FINOPS_INGEST_INTERVAL_SECONDS)
