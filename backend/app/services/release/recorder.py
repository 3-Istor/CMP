"""
Background release recorder.

The history is otherwise only synced while someone has the Release tab open,
which leaves holes in it and in the delivery metrics built on it.
"""

import asyncio
import logging

from app.core.database import SessionLocal
from app.models.deployment import Deployment, ProviderType
from app.services.kube_client import KubeUnavailableError
from app.services.release import sync
from app.services.release.source import KubeReleaseSource, ReleaseSource

logger = logging.getLogger(__name__)

RECORD_INTERVAL_SECONDS = 30


def record_all(source: ReleaseSource) -> int:
    """Sync the history of every Kubernetes app, return how many have a Rollout."""
    recorded = 0
    with SessionLocal() as db:
        deployments = (
            db.query(Deployment)
            .filter(Deployment.provider_type == ProviderType.KUBERNETES)
            .all()
        )
        for deployment in deployments:
            try:
                _, snapshot, _ = sync.compute(db, deployment, source)
            except KubeUnavailableError as exc:
                logger.warning(
                    "Release recorder: cluster unreachable: %s", exc
                )
                return recorded
            except Exception:  # pylint: disable=broad-exception-caught
                # One malformed Rollout must not stop the others being recorded.
                logger.exception(
                    "Release recorder failed for deployment %s", deployment.id
                )
                db.rollback()
                continue
            if snapshot is not None:
                recorded += 1
    return recorded


async def release_recorder_loop() -> None:
    source = KubeReleaseSource()
    while True:
        try:
            await asyncio.to_thread(record_all, source)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Release recorder pass failed")
        await asyncio.sleep(RECORD_INTERVAL_SECONDS)
