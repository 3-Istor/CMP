"""
Background release recorder.

The history is otherwise only synced while someone has the Release tab open,
which leaves holes in it and in the delivery metrics built on it.
"""

import asyncio
import logging

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.environment import AppEnvironment
from app.models.project import TargetCloud
from app.services.clusters.state import cluster_states, project_cloud
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
)
from app.services.release import sync
from app.services.release.rolling import read_rolling, sync_rolling
from app.services.release.route_reconciler import reconcile_route
from app.services.release.source import KubeReleaseSource, ReleaseSource

logger = logging.getLogger(__name__)

RECORD_INTERVAL_SECONDS = 30
# A canary shifting or holding traffic: re-syncing its route would reset the weights.
MOVING_PHASES = {"Progressing", "Paused"}


def _environments(db: Session, deployment: Deployment) -> list[str]:
    staging = db.get(AppEnvironment, (deployment.id, "staging"))
    return (
        ["prod", "staging"]
        if staging and staging.state == "ready"
        else ["prod"]
    )


def _record_rolling(db: Session, deployment: Deployment, cloud: TargetCloud):
    # Own guard: a canary app's route must still reconcile when this fails.
    # Also read for canary apps, a fullstack one keeps its backend on rolling.
    try:
        sync_rolling(
            db,
            deployment.id,
            read_rolling(deployment, cloud, _environments(db, deployment)),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Rolling history skipped for %s: %s", deployment.id, exc
        )
        db.rollback()


def record_all(source: ReleaseSource) -> int:
    """Sync the history of every Kubernetes app, return how many have a Rollout."""
    recorded = 0
    with SessionLocal() as db:
        deployments = (
            db.query(Deployment)
            .filter(
                Deployment.provider_type == ProviderType.KUBERNETES,
                Deployment.status != DeploymentStatus.DELETED,
            )
            .all()
        )
        for deployment in deployments:
            cloud = project_cloud(db, deployment.project_id)
            try:
                view, snapshot, _ = sync.compute(db, deployment, source)
            except ClusterNotConnectedError as exc:
                logger.debug("Release recorder: skipped, %s", exc)
                continue
            except KubeUnavailableError as exc:
                logger.warning(
                    "Release recorder: %s cluster unreachable: %s",
                    cloud.value,
                    exc,
                )
                if cloud == TargetCloud.ONPREM:
                    return recorded
                continue
            except Exception:  # pylint: disable=broad-exception-caught
                # One malformed Rollout must not stop the others being recorded.
                logger.exception(
                    "Release recorder failed for deployment %s", deployment.id
                )
                db.rollback()
                continue
            _record_rolling(db, deployment, cloud)
            if deployment.project_id:
                try:
                    reconcile_route(
                        deployment.project_id,
                        deployment.name,
                        canary_moving=view.phase in MOVING_PHASES,
                        cloud=cloud,
                    )
                except (KubeUnavailableError, ClusterNotConnectedError) as exc:
                    logger.warning(
                        "Route reconcile skipped for %s: %s",
                        deployment.id,
                        exc,
                    )
            if snapshot is not None:
                recorded += 1
    return recorded


async def release_recorder_loop() -> None:
    source = KubeReleaseSource()
    while True:
        try:
            await cluster_states()
            await asyncio.to_thread(record_all, source)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Release recorder pass failed")
        await asyncio.sleep(RECORD_INTERVAL_SECONDS)
