"""The access review background job: auto start, deadlines, inactivity."""

import asyncio
import logging
import time

from fastapi.concurrency import run_in_threadpool

from app.core.database import SessionLocal
from app.services.access_review import accounts, campaigns, directory
from app.services.access_review.notices import Notice, deliver

logger = logging.getLogger(__name__)

TICK_SECONDS = 3600
INACTIVITY_SECONDS = 86400


def _campaign_tick() -> list[Notice]:
    now = campaigns.utcnow()
    with SessionLocal() as db:
        try:
            out = campaigns.maybe_auto_start(db, now)
        except campaigns.ReviewError as exc:
            logger.error("Scheduled review not started: %s", exc.detail)
            out = []
        return out + campaigns.escalate(db, now)


def _inactivity_tick() -> list[Notice]:
    with SessionLocal() as db:
        try:
            return accounts.check_inactive(db, campaigns.utcnow())
        except directory.DirectoryError as exc:
            # No login data, no verdict: nobody is reported or disabled.
            logger.warning("Inactivity check skipped: %s", exc)
            return []


async def access_review_loop() -> None:
    last_inactivity: float | None = None
    while True:
        try:
            items = await run_in_threadpool(_campaign_tick)
            if (
                last_inactivity is None
                or time.monotonic() - last_inactivity >= INACTIVITY_SECONDS
            ):
                last_inactivity = time.monotonic()
                items += await run_in_threadpool(_inactivity_tick)
            await deliver(items)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Access review job error: %s", exc, exc_info=True)
        await asyncio.sleep(TICK_SECONDS)
