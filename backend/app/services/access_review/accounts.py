"""
Login tracking and inactive accounts.

An account without a login for INACTIVE_AFTER_DAYS is reported once; if it
is still inactive INACTIVE_GRACE_DAYS later and INACTIVE_DISABLE_ENABLED is
on, it is disabled (``enabled=false``: groups, credentials and history are
kept, enabling it again restores everything). Demo, break-glass and service
accounts are never touched, see :func:`excluded`.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.access_review import AccountActivity
from app.services.access_review import directory
from app.services.access_review.notices import Inbox, Notice
from app.services.audit import record_system_event

logger = logging.getLogger(__name__)

LINK = "/access-reviews"


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def excluded(user: dict) -> bool:
    username = user.get("username") or ""
    if user.get("serviceAccountClientLink"):
        return True
    if username in _csv(settings.INACTIVE_EXCLUDED_USERS):
        return True
    return any(
        username.startswith(prefix)
        for prefix in _csv(settings.INACTIVE_EXCLUDED_PREFIXES)
    )


def refresh_logins(
    db: Session, now: datetime
) -> tuple[list[dict], dict[str, AccountActivity]]:
    """
    Fold the LOGIN events Keycloak still holds into ``account_activity``.
    Raises DirectoryError when Keycloak cannot say who logged in.
    """
    retention = directory.event_retention()
    lookback = min(retention or directory.LOOKBACK_CAP, directory.LOOKBACK_CAP)
    seen = directory.login_events(now - lookback)
    realm_users = directory.users()
    rows = {r.user_id: r for r in db.query(AccountActivity).all()}
    for user in realm_users:
        row = rows.get(user["id"])
        if row is None:
            # The event store reaches back this far, not further: no login
            # before it proves nothing.
            row = AccountActivity(
                user_id=user["id"],
                username=user.get("username", ""),
                tracked_since=now - lookback,
            )
            db.add(row)
            rows[user["id"]] = row
        row.username = user.get("username", row.username)
        login = seen.get(user["id"])
        if login and (row.last_login_at is None or login > row.last_login_at):
            row.last_login_at = login
    db.commit()
    return realm_users, rows


def last_seen(row: AccountActivity, user: dict) -> datetime:
    candidates = [row.tracked_since]
    if row.last_login_at:
        candidates.append(row.last_login_at)
    created = directory.from_millis(user.get("createdTimestamp"))
    if created:
        candidates.append(created)
    return max(candidates)


def _inactive_notice(
    kind: str, username: str, seen: datetime, now: datetime
) -> Notice:
    days = (now - seen).days
    return Notice(
        kind=kind,
        fields=(
            ("User", username),
            ("Last seen", f"{seen.isoformat(timespec='minutes')}Z"),
        ),
        link=LINK,
        inbox=(
            Inbox(
                title_key=f"access_review_{kind}",
                params={"user": username, "days": days},
                link=LINK,
                to_infra=True,
            ),
        ),
    )


def check_inactive(db: Session, now: datetime) -> list[Notice]:
    realm_users, rows = refresh_logins(db, now)
    threshold = timedelta(days=settings.INACTIVE_AFTER_DAYS)
    grace = timedelta(days=settings.INACTIVE_GRACE_DAYS)
    out: list[Notice] = []
    for user in realm_users:
        if excluded(user) or not user.get("enabled", True):
            continue
        row = rows[user["id"]]
        if row.disabled_at is not None:
            # Enabled again from the Keycloak console: a fresh start.
            row.disabled_at = None
            row.flagged_at = None
            row.tracked_since = now
            continue
        seen = last_seen(row, user)
        if now - seen < threshold:
            row.flagged_at = None
            continue
        try:
            live = directory.last_session_access(user["id"])
        except directory.DirectoryError as exc:
            logger.warning("Sessions of %s unknown: %s", row.username, exc)
            continue
        if live and now - live < threshold:
            row.last_login_at = max(row.last_login_at or live, live)
            row.flagged_at = None
            continue
        if row.flagged_at is None:
            row.flagged_at = now
            record_system_event(
                "access_review.account.flag",
                row.username,
                {"user_id": row.user_id, "last_seen": seen},
            )
            out.append(_inactive_notice("inactive", row.username, seen, now))
        elif (
            settings.INACTIVE_DISABLE_ENABLED and now - row.flagged_at >= grace
        ):
            try:
                directory.set_enabled(row.user_id, False)
            except directory.DirectoryError as exc:
                logger.error("Cannot disable %s: %s", row.username, exc)
                record_system_event(
                    "access_review.account.disable",
                    row.username,
                    {"user_id": row.user_id, "error": str(exc)},
                    succeeded=False,
                )
                continue
            row.disabled_at = now
            record_system_event(
                "access_review.account.disable",
                row.username,
                {"user_id": row.user_id, "last_seen": seen},
            )
            out.append(_inactive_notice("disabled", row.username, seen, now))
    db.commit()
    return out


def enable(db: Session, user_id: str, now: datetime) -> AccountActivity:
    """
    Keep an account: enable it again if it was disabled, and restart its
    inactivity clock either way. Raises LookupError or DirectoryError.
    """
    row = db.get(AccountActivity, user_id)
    if row is None or (row.flagged_at is None and row.disabled_at is None):
        raise LookupError(user_id)
    if row.disabled_at is not None:
        directory.set_enabled(user_id, True)
    row.disabled_at = None
    row.flagged_at = None
    row.tracked_since = now
    db.commit()
    return row


def flagged(db: Session) -> list[AccountActivity]:
    return (
        db.query(AccountActivity)
        .filter(
            AccountActivity.flagged_at.isnot(None)
            | AccountActivity.disabled_at.isnot(None)
        )
        .order_by(AccountActivity.username)
        .all()
    )
