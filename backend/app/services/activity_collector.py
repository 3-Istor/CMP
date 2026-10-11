"""
Reads the audit sources out of Loki a minute at a time and keeps them in the
CMP database, attributed to their project.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.audit import ActivityCursor, ActivityRecord
from app.services import activity_sources as src
from app.services import metrics_isolation
from app.services.kube_client import KubeUnavailableError

logger = logging.getLogger(__name__)

TICK = 60
# Lines reach Loki a few seconds after they happen.
LAG = timedelta(seconds=60)
CHUNK = timedelta(hours=1)
PAGE = 5000

QUERIES = {
    "kubernetes": src.KUBERNETES_QUERY,
    "vault": src.VAULT_QUERY,
    "keycloak": src.KEYCLOAK_QUERY,
    "network": src.NETWORK_QUERY,
}


def _record(event: src.Event) -> ActivityRecord:
    return ActivityRecord(
        external_id=event.id,
        source=event.source,
        time=event.time,
        project=event.project,
        app=event.app,
        actor=event.actor[:255],
        action=event.action[:64],
        notable=event.notable,
        is_read=event.is_read,
        target=event.target[:512],
        outcome=event.outcome,
        status_code=event.status_code,
        source_ip=event.source_ip,
        details=json.dumps(event.details, default=str),
    )


def store(events: list[src.Event]) -> int:
    """Insert the events not stored yet; return how many were new."""
    if not events:
        return 0
    with SessionLocal() as db:
        ids = [e.id for e in events]
        known = set(
            db.scalars(
                select(ActivityRecord.external_id).where(
                    ActivityRecord.external_id.in_(ids)
                )
            )
        )
        new = {e.id: e for e in events if e.id not in known}
        db.add_all(_record(e) for e in new.values())
        db.commit()
        return len(new)


def _cursor(source: str) -> datetime:
    with SessionLocal() as db:
        row = db.get(ActivityCursor, source)
        if row:
            until = row.until
            return (
                until if until.tzinfo else until.replace(tzinfo=timezone.utc)
            )
    return datetime.now(timezone.utc) - timedelta(
        days=settings.ACTIVITY_BACKFILL_DAYS
    )


def _save_cursor(source: str, until: datetime) -> None:
    with SessionLocal() as db:
        row = db.get(ActivityCursor, source)
        if row:
            row.until = until
        else:
            db.add(ActivityCursor(source=source, until=until))
        db.commit()


def normalise(
    source: str, ns_projects: dict[str, str], when: datetime, line: str
) -> src.Event | None:
    projects = set(ns_projects.values())
    if source == "kubernetes":
        return src.kubernetes_event(ns_projects, when, line)
    if source == "vault":
        return src.vault_event(projects, when, line)
    if source == "network":
        return src.network_event(ns_projects, when, line)
    return src.keycloak_event(projects, when, line)


async def collect_source(source: str, ns_projects: dict[str, str]) -> int:
    start = await run_in_threadpool(_cursor, source)
    end = datetime.now(timezone.utc) - LAG
    added = 0
    while start < end:
        stop = min(start + CHUNK, end)
        lines = await src.loki_lines(
            QUERIES[source], start, stop, PAGE, direction="forward"
        )
        if len(lines) == PAGE:
            # More than a page in this chunk: resume after the newest line read.
            stop = lines[0][0] + timedelta(microseconds=1)
        events = [
            e
            for when, _, line in lines
            if (e := normalise(source, ns_projects, when, line))
        ]
        added += await run_in_threadpool(store, events)
        await run_in_threadpool(_save_cursor, source, stop)
        start = stop
    return added


async def collect_once() -> None:
    ns_projects = await run_in_threadpool(src.namespace_projects)
    if settings.METRICS_PROJECT_KEY and ns_projects:
        try:
            await run_in_threadpool(metrics_isolation.sync, ns_projects)
        except KubeUnavailableError as exc:
            logger.warning("vmauth config not written: %s", exc)
    for source in QUERIES:
        try:
            added = await collect_source(source, ns_projects)
            if added:
                logger.info(
                    "Activity collector: %d new %s events", added, source
                )
        except src.LokiUnavailableError as exc:
            logger.warning(
                "Activity collector: %s unavailable: %s", source, exc
            )
    try:
        deploys = await run_in_threadpool(src.deployment_events)
        await run_in_threadpool(store, deploys)
    except KubeUnavailableError as exc:
        logger.warning("Activity collector: Argo CD unavailable: %s", exc)


async def activity_collector_loop() -> None:
    logger.info("Activity collector started")
    while True:
        try:
            await collect_once()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Activity collector error: %s", exc, exc_info=True)
        await asyncio.sleep(TICK)
