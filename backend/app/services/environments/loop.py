"""Moves promotions along, starts automatic ones and expires stuck runs."""

import asyncio
import logging

from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.environment import AppEnvironment, Promotion, PromotionPolicy
from app.services.environments import gitops, observe, promotion, runs
from app.services.kube_client import KubeUnavailableError

logger = logging.getLogger(__name__)

TICK_SECONDS = 15.0


async def tick(tick_seconds: float = TICK_SECONDS) -> None:
    with SessionLocal() as db:
        runs.expire(db)
        for item in (
            db.query(Promotion)
            .filter(Promotion.status.in_(promotion.ACTIVE))
            .all()
        ):
            try:
                await promotion.advance(db, item, tick_seconds)
            except (KubeUnavailableError, gitops.GitOpsError) as exc:
                logger.warning("promotion %s waits: %s", item.id, exc)
        await _auto_promote(db)


async def _auto_promote(db) -> None:
    """Start a promotion for a new release in staging when the app asks for it."""
    for policy in db.query(PromotionPolicy).filter_by(auto_promote=True).all():
        row = db.get(AppEnvironment, (policy.deployment_id, "staging"))
        deployment = db.get(Deployment, policy.deployment_id)
        if row is None or row.state != "ready" or deployment is None:
            continue
        if promotion.active_promotion(db, deployment.id):
            continue
        try:
            staging = await asyncio.to_thread(
                observe.of, deployment, "staging"
            )
        except KubeUnavailableError:
            continue
        if not staging.converged:
            continue
        tried = (
            db.query(Promotion)
            .filter_by(deployment_id=deployment.id)
            .order_by(Promotion.created_at.desc())
            .limit(5)
            .all()
        )
        if any(p.digests == staging.digests for p in tried):
            continue
        try:
            started = await promotion.start(
                db, deployment, "auto-promote", actor_is_admin=True
            )
        except promotion.PromotionRefused:
            continue
        logger.info(
            "auto-promotion %s started for %s", started.id, deployment.name
        )


async def environment_loop() -> None:
    while True:
        try:
            await tick()
        except Exception:  # the loop must survive one bad tick
            logger.exception("environment loop tick failed")
        await asyncio.sleep(TICK_SECONDS)
