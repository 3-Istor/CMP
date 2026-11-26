"""
Deploy markers and the first two DORA measures of an app (D18), from the
release history the CMP already keeps (release_events): every rollout of
prod it saw, canary or rolling, and how it ended.

A change "failed" when its rollout did: a canary aborted (by the analysis
or by a person) or a rolling update past its progress deadline. Rows still
running or superseded by a newer revision are neither.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.release import ReleaseEvent

SUCCEEDED = frozenset({"promoted", "deployed"})
FAILED = frozenset({"aborted_auto", "aborted_manual", "failed"})
DORA_DAYS = (7, 30, 90)


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone of DateTime(timezone=True) columns.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def version_of(image: str | None) -> str | None:
    """The tag of ghcr.io/org/app:1.4.0@sha256:..., else a short digest."""
    if not image:
        return None
    name, _, digest = image.partition("@")
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        return last.split(":", 1)[1]
    return digest.split(":", 1)[-1][:12] or None


def _events(
    db: Session,
    deployment_id: int,
    since: datetime,
    environment: str = "prod",
) -> list[ReleaseEvent]:
    return (
        db.query(ReleaseEvent)
        .filter(
            ReleaseEvent.deployment_id == deployment_id,
            ReleaseEvent.environment == environment,
            ReleaseEvent.started_at >= since,
        )
        .order_by(ReleaseEvent.started_at)
        .all()
    )


def markers(
    db: Session, deployment_id: int, since: datetime
) -> list[dict[str, Any]]:
    return [
        {
            "time": _aware(e.started_at),
            "finished_at": _aware(e.finished_at) if e.finished_at else None,
            "version": version_of(e.image),
            "strategy": e.strategy,
            "outcome": e.outcome,
            "component": e.component,
        }
        for e in _events(db, deployment_id, since)
        if e.outcome != "superseded"
    ]


def dora(
    db: Session,
    deployment_id: int,
    days: int,
    now: datetime,
    environment: str = "prod",
) -> dict[str, Any]:
    events = _events(
        db, deployment_id, now - timedelta(days=days), environment
    )
    deploys = sum(1 for e in events if e.outcome in SUCCEEDED)
    failed = sum(1 for e in events if e.outcome in FAILED)
    attempts = deploys + failed
    return {
        "days": days,
        "deployments": deploys,
        "failed_changes": failed,
        "deploys_per_week": round(deploys * 7 / days, 2),
        "change_failure_rate": (
            round(failed / attempts, 4) if attempts else None
        ),
    }
