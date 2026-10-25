"""Cluster state machine: up/down transitions on the public clouds."""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.cluster import Cluster, ClusterState
from app.models.project import TargetCloud

PUBLIC_CLOUDS = (TargetCloud.AWS, TargetCloud.GCP)

_ALLOWED = {
    "up": (ClusterState.DOWN, ClusterState.ERROR),
    "down": (ClusterState.UP, ClusterState.ERROR),
}
_BUSY = {"up": ClusterState.STARTING, "down": ClusterState.STOPPING}
_DONE = {"up": ClusterState.UP, "down": ClusterState.DOWN}


class TransitionError(Exception):
    """Operation not allowed from the current state (maps to HTTP 409)."""


def _row(
    db: Session, cloud: TargetCloud, lock: bool = False
) -> Cluster | None:
    q = db.query(Cluster).filter(Cluster.cloud == cloud)
    return (q.with_for_update() if lock else q).one_or_none()


def begin(
    db: Session, cloud: TargetCloud, operation: str, user: str
) -> Cluster:
    if cloud not in PUBLIC_CLOUDS:
        raise TransitionError(f"{cloud.value} is not a public cloud")
    if operation not in _ALLOWED:
        raise ValueError(f"unknown operation {operation!r}")
    row = _row(db, cloud, lock=True)
    if row is None:
        row = Cluster(cloud=cloud, state=ClusterState.DOWN)
        db.add(row)
    if row.state not in _ALLOWED[operation]:
        raise TransitionError(
            f"cannot {operation} {cloud.value} while {row.state.value}"
        )
    now = datetime.now(timezone.utc)
    row.state = _BUSY[operation]
    row.operation = operation
    row.step = None
    row.done = row.total = 0
    row.started_at = now.replace(tzinfo=None)
    row.started_by = user
    row.attempts = 1
    row.last_error = None
    row.log_path = f"data/cluster_ops/{cloud.value}-{now:%Y%m%dT%H%M%SZ}.log"
    db.commit()
    return row


def set_step(
    db: Session, cloud: TargetCloud, step: str, done: int = 0, total: int = 0
) -> None:
    row = _row(db, cloud)
    row.step, row.done, row.total = step, done, total
    db.commit()


def finish(
    db: Session, cloud: TargetCloud, ok: bool, error: str | None = None
) -> None:
    row = _row(db, cloud)
    row.state = _DONE[row.operation] if ok else ClusterState.ERROR
    row.last_error = None if ok else error
    db.commit()


def ensure_cluster_up(db: Session, cloud: TargetCloud) -> None:
    """409 unless the cloud's cluster is up; on-prem is always available."""
    if cloud not in PUBLIC_CLOUDS:
        return
    row = _row(db, cloud)
    state = row.state if row else ClusterState.DOWN
    if state != ClusterState.UP:
        raise HTTPException(
            409,
            f"The {cloud.value.upper()} cluster is {state.value}; "
            "deployments resume once it is up.",
        )
