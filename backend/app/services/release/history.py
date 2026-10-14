"""
Release history: one ``release_events`` row per attempt at a revision.

Rows are written when the CMP computes a view (a page open, the SSE poll)
and when someone acts on a release. Each call compares what it sees with the
latest row and writes only a transition, so polling every two seconds stays
idempotent.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.release import ReleaseEvent
from app.schemas.release import ReleaseView
from app.services.release.view import ReleaseHint


def latest_event(db: Session, deployment_id: int) -> ReleaseEvent | None:
    return (
        db.query(ReleaseEvent)
        .filter(ReleaseEvent.deployment_id == deployment_id)
        .order_by(ReleaseEvent.id.desc())
        .first()
    )


def list_events(
    db: Session, deployment_id: int, limit: int = 50
) -> list[ReleaseEvent]:
    return (
        db.query(ReleaseEvent)
        .filter(ReleaseEvent.deployment_id == deployment_id)
        .order_by(ReleaseEvent.id.desc())
        .limit(limit)
        .all()
    )


def hint_from(event: ReleaseEvent | None, revision: int | None) -> ReleaseHint:
    """What the latest row adds to a view of the same revision."""
    if event is None or event.revision != revision:
        return ReleaseHint()
    skipped: set[int] = set()
    skipped_from: int | None = None
    for actor in event.actors or []:
        step = actor.get("step")
        if step is None:
            continue
        if actor["action"] == "skip":
            skipped.add(step)
        elif actor["action"] == "promote_full":
            skipped_from = (
                step if skipped_from is None else min(step, skipped_from)
            )
    return ReleaseHint(
        started_at=_aware(event.started_at),
        forced=event.forced,
        skipped=skipped,
        skipped_from=skipped_from,
        reached_step=event.reached_step,
    )


def _aware(value: datetime | None) -> datetime | None:
    # SQLite drops the timezone of DateTime(timezone=True) columns.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _steps(view: ReleaseView) -> list[dict]:
    return [s.model_dump(mode="json", by_alias=True) for s in view.steps]


def _statuses(steps: list[dict] | None) -> list[str]:
    # Durations tick on every poll; only a status change is worth a write.
    return [s.get("status") for s in steps or []]


def _finish(event: ReleaseEvent, outcome: str, view: ReleaseView | None):
    event.outcome = outcome
    event.finished_at = datetime.now(timezone.utc)
    if view is not None and view.revision == event.revision:
        event.steps = _steps(view)


def _open(
    db: Session, deployment_id: int, view: ReleaseView, outcome: str
) -> ReleaseEvent:
    now = datetime.now(timezone.utc)
    event = ReleaseEvent(
        deployment_id=deployment_id,
        revision=view.revision,
        image=view.canary_image,
        strategy=view.strategy,
        started_at=view.started_at or now,
        finished_at=None if outcome == "running" else now,
        outcome=outcome,
        forced=False,
        reached_step=view.current_step_index,
        steps=_steps(view),
        actors=[],
    )
    db.add(event)
    return event


def sync_event(
    db: Session, deployment_id: int, view: ReleaseView
) -> ReleaseEvent | None:
    """
    Bring the history in line with *view* and return the current row.

    Commits only when something changed.
    """
    if not view.enabled or view.revision is None:
        return None
    latest = latest_event(db, deployment_id)
    running = latest if latest and latest.outcome == "running" else None
    same = latest is not None and latest.revision == view.revision
    aborted = view.phase == "Aborted"
    changed = False

    if view.in_progress and not aborted:
        if running and same:
            reached = max(running.reached_step or 0, view.current_step_index)
            if reached != running.reached_step or _statuses(running.steps) != [
                s.status for s in view.steps
            ]:
                running.reached_step = reached
                running.steps = _steps(view)
                changed = True
        else:
            # A newer revision, or a retry of an aborted one.
            if running:
                _finish(running, "superseded", None)
            latest = _open(db, deployment_id, view, "running")
            changed = True
    elif view.in_progress and aborted:
        if running and same:
            manual = any(a["action"] == "abort" for a in running.actors or [])
            _finish(
                running, "aborted_manual" if manual else "aborted_auto", view
            )
            changed = True
        elif not same:
            if running:
                _finish(running, "superseded", None)
            latest = _open(db, deployment_id, view, "aborted_auto")
            changed = True
    elif running:
        _finish(running, "promoted" if same else "superseded", view)
        if same:
            running.reached_step = len(view.steps)
        changed = True

    if changed:
        db.commit()
    return latest


def record_action(
    db: Session,
    event: ReleaseEvent,
    user: str,
    action: str,
    reason: str | None,
    step: int | None,
    forced: bool,
) -> None:
    event.actors = [
        *(event.actors or []),
        {
            "user": user,
            "action": action,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
            "step": step,
        },
    ]
    if forced:
        event.forced = True
    # The poll may have seen the abort land before this call recorded who
    # asked for it.
    if action == "abort" and event.outcome == "aborted_auto":
        event.outcome = "aborted_manual"
    db.commit()
