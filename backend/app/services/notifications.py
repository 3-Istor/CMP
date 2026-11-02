"""
Notification centre: who hears about what.

Producers call :func:`notify_project` or :func:`notify_users` at the point an
event is already recorded. Recipients are resolved here and nowhere else, so
no producer can address a user outside the project concerned. Both functions
swallow their own failures: a missed notification must never undo the change
it reports.
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.core.database import SessionLocal
from app.core.roles import ProjectRole, at_least
from app.models.notification import Notification
from app.models.project import Project
from app.services import keycloak_service

logger = logging.getLogger(__name__)

READ_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 90


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def project_recipients(
    project: str, minimum: ProjectRole | None = None
) -> set[str]:
    """Usernames holding at least *minimum* on *project* (everyone if None)."""
    recipients: set[str] = set()
    for member in keycloak_service.list_project_members(project):
        role = ProjectRole(member["role"])
        if minimum is None or at_least(role, minimum):
            recipients.add(member["username"])

    with SessionLocal() as db:
        owner = (
            db.query(Project.owner_username)
            .filter(Project.project_name == project)
            .scalar()
        )
    if owner:
        recipients.add(owner)
    return recipients


def notify_users(
    users: Iterable[str],
    *,
    kind: str,
    title_key: str,
    params: dict | None = None,
    link: str | None = None,
    project: str | None = None,
    app: str | None = None,
) -> int:
    """Store one notification per user. Returns how many were written."""
    keys = sorted({u for u in users if u})
    if not keys:
        return 0
    try:
        now = utcnow()
        with SessionLocal() as db:
            db.add_all(
                Notification(
                    user_key=key,
                    project=project,
                    app=app,
                    kind=kind,
                    title_key=title_key,
                    params=params or {},
                    link=link,
                    created_at=now,
                )
                for key in keys
            )
            db.commit()
        return len(keys)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Could not store notifications: %s", exc, exc_info=True)
        return 0


def notify_project(
    project: str,
    *,
    kind: str,
    title_key: str,
    params: dict | None = None,
    link: str | None = None,
    app: str | None = None,
    minimum: ProjectRole | None = None,
) -> int:
    """Notify the members of *project* holding at least *minimum*."""
    try:
        recipients = project_recipients(project, minimum)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Notification for project '%s' skipped, members unknown: %s",
            project,
            exc,
        )
        return 0
    return notify_users(
        recipients,
        kind=kind,
        title_key=title_key,
        params=params,
        link=link,
        project=project,
        app=app,
    )


def purge_expired() -> int:
    """Drop read notifications after 30 days and every one after 90."""
    now = utcnow()
    with SessionLocal() as db:
        result = db.execute(
            delete(Notification).where(
                (
                    Notification.created_at
                    < now - timedelta(days=MAX_RETENTION_DAYS)
                )
                | (
                    Notification.read_at.isnot(None)
                    & (
                        Notification.created_at
                        < now - timedelta(days=READ_RETENTION_DAYS)
                    )
                )
            )
        )
        db.commit()
        return result.rowcount or 0
