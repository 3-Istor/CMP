"""Advances running project moves, and serializes changes per project."""

import asyncio
import logging
from collections import defaultdict

from app.core.database import SessionLocal
from app.models.project_move import ProjectMove
from app.services.environments.loop import TICK_SECONDS
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
)
from app.services.moves import steps

logger = logging.getLogger(__name__)

# ponytail: single backend process (one pod, Recreate); a DB-level guard
# (partial unique index on active rows) if the backend ever scales out.
_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def project_lock(project: str) -> asyncio.Lock:
    return _locks[project]


async def tick() -> None:
    with SessionLocal() as db:
        ids = [
            move_id
            for (move_id,) in db.query(ProjectMove.id).filter(
                ProjectMove.status == "running"
            )
        ]
        for move_id in ids:
            move = db.get(ProjectMove, move_id)
            async with project_lock(move.project):
                db.refresh(move)  # a cancel may have landed while waiting
                try:
                    await steps.advance(db, move)
                except (ClusterNotConnectedError, KubeUnavailableError) as exc:
                    logger.warning("move %s waits: %s", move_id, exc)


async def move_loop() -> None:
    while True:
        try:
            await tick()
        except Exception:  # the loop must survive one bad tick
            logger.exception("move loop tick failed")
        await asyncio.sleep(TICK_SECONDS)
