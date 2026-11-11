"""
Life of a just-in-time access request.

    pending --approve--> active --expiry--> expired
       |                   \\--end-------> revoked
       |--deny---> denied
       |--cancel-> cancelled
       \\--ttl----> lapsed
    approve with Keycloak failing -> failed

The row turns active before the Keycloak call, so the sweep never mistakes
a member being added for one added by hand. Every transition is journalled
(the audit middleware for REST calls, record_system_event for the sweep) and
announced on Discord and in the notification centre.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.access import AccessRequest
from app.services import keycloak_service, notifications
from app.services.access import catalog, groups
from app.services.audit import record_system_event
from app.services.authz import PLATFORM_ADMIN_GROUP

logger = logging.getLogger(__name__)

PENDING = "pending"
ACTIVE = "active"
OPEN = (PENDING, ACTIVE)
SWEEP_SECONDS = 30
REASON_MIN, REASON_MAX = 10, 500


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AccessError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Announcement:  # pylint: disable=too-many-instance-attributes
    """What happened, for Discord and the notification centre."""

    kind: str
    request_id: int
    elevation: str
    requester: str
    actor: str
    reason: str = ""
    expires_at: datetime | None = None
    note: str = ""


def announcement(kind: str, row: AccessRequest, actor: str) -> Announcement:
    return Announcement(
        kind=kind,
        request_id=row.id,
        elevation=row.elevation,
        requester=row.requester,
        actor=actor,
        reason=row.reason,
        expires_at=row.expires_at,
        note=row.decision_note or "",
    )


def _get(db: Session, request_id: int) -> AccessRequest:
    row = db.get(AccessRequest, request_id)
    if row is None:
        raise AccessError(404, "Access request not found.")
    return row


def create(
    db: Session, requester: str, elevation: str, reason: str, minutes: int
) -> AccessRequest:
    entry = catalog.get(elevation)
    if entry is None:
        raise AccessError(422, f"Unknown elevation '{elevation}'.")
    reason = reason.strip()
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise AccessError(
            422,
            f"The reason must be {REASON_MIN} to {REASON_MAX} characters.",
        )
    if not catalog.MIN_MINUTES <= minutes <= catalog.max_minutes():
        raise AccessError(
            422,
            f"The duration must be {catalog.MIN_MINUTES} to "
            f"{catalog.max_minutes()} minutes.",
        )
    already = (
        db.query(AccessRequest.id)
        .filter(
            AccessRequest.requester == requester,
            AccessRequest.elevation == elevation,
            AccessRequest.status.in_(OPEN),
        )
        .first()
    )
    if already:
        raise AccessError(
            409, "You already have a pending or active request for this."
        )
    row = AccessRequest(
        elevation=elevation,
        requester=requester,
        reason=reason,
        duration_minutes=minutes,
        status=PENDING,
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    return row


def _claim_pending(
    db: Session, row: AccessRequest, values: dict
) -> AccessRequest:
    """Move a pending row on, once: a second approver gets a 409."""
    result = db.execute(
        update(AccessRequest)
        .where(AccessRequest.id == row.id, AccessRequest.status == PENDING)
        .values(**values)
    )
    db.commit()
    if result.rowcount != 1:
        raise AccessError(409, "This request was already decided.")
    db.refresh(row)
    return row


def approve(db: Session, request_id: int, approver: str) -> AccessRequest:
    row = _get(db, request_id)
    if approver == row.requester:
        raise AccessError(
            403, "Four eyes: another platform admin must approve."
        )
    if row.status != PENDING:
        raise AccessError(409, "This request was already decided.")
    now = utcnow()
    _claim_pending(
        db,
        row,
        {
            "status": ACTIVE,
            "decided_by": approver,
            "decided_at": now,
            "activated_at": now,
            "expires_at": now + timedelta(minutes=row.duration_minutes),
        },
    )
    group = catalog.ELEVATIONS[row.elevation].group
    try:
        groups.add_member(row.requester, group)
    except groups.GroupError as exc:
        logger.error("Grant %s not applied: %s", row.id, exc)
        row.status = "failed"
        row.ended_at = utcnow()
        db.commit()
        try:
            groups.remove_member(row.requester, group)
        except groups.GroupError:
            pass
        raise AccessError(
            502, "Keycloak refused the change, the grant is not active."
        ) from exc
    return row


def deny(
    db: Session, request_id: int, approver: str, note: str
) -> AccessRequest:
    row = _get(db, request_id)
    if approver == row.requester:
        raise AccessError(403, "Cancel your own request instead.")
    return _claim_pending(
        db,
        row,
        {
            "status": "denied",
            "decided_by": approver,
            "decided_at": utcnow(),
            "decision_note": note.strip()[:REASON_MAX] or None,
        },
    )


def cancel(db: Session, request_id: int, actor: str) -> AccessRequest:
    row = _get(db, request_id)
    if actor != row.requester:
        raise AccessError(403, "Only the requester can cancel.")
    return _claim_pending(
        db,
        row,
        {"status": "cancelled", "ended_at": utcnow(), "ended_by": actor},
    )


def _close(db: Session, row: AccessRequest, status: str, actor: str) -> None:
    """Remove the membership, then mark the row. Raises GroupError."""
    groups.remove_member(
        row.requester, catalog.ELEVATIONS[row.elevation].group
    )
    row.status = status
    row.ended_at = utcnow()
    row.ended_by = actor
    db.commit()


def end(db: Session, request_id: int, actor: str) -> AccessRequest:
    """End an active grant early: its holder, or any platform admin."""
    row = _get(db, request_id)
    if row.status != ACTIVE:
        raise AccessError(409, "This grant is not active.")
    try:
        _close(db, row, "revoked", actor)
    except groups.GroupError as exc:
        logger.error("Grant %s not removed: %s", row.id, exc)
        raise AccessError(
            502, "Keycloak refused the removal, the grant is still active."
        ) from exc
    return row


def sweep(db: Session) -> list[Announcement]:
    """
    Expire grants past their end, lapse requests nobody decided on, and
    remove anyone found in an elevated group without an active grant.
    """
    now = utcnow()
    out: list[Announcement] = []

    for row in (
        db.query(AccessRequest)
        .filter(
            AccessRequest.status == ACTIVE, AccessRequest.expires_at <= now
        )
        .all()
    ):
        try:
            _close(db, row, "expired", "cmp")
        except groups.GroupError as exc:
            # Kept active: the next sweep tries again.
            logger.error("Grant %s could not be expired: %s", row.id, exc)
            continue
        record_system_event(
            "access.expire",
            f"{row.requester}:{row.elevation}",
            {"request_id": row.id, "elevation": row.elevation},
        )
        out.append(announcement("expired", row, "cmp"))

    cutoff = now - timedelta(minutes=settings.ACCESS_REQUEST_TTL_MINUTES)
    for row in (
        db.query(AccessRequest)
        .filter(
            AccessRequest.status == PENDING, AccessRequest.created_at < cutoff
        )
        .all()
    ):
        row.status = "lapsed"
        row.ended_at = now
        db.commit()
        record_system_event(
            "access.lapse",
            f"{row.requester}:{row.elevation}",
            {"request_id": row.id, "elevation": row.elevation},
        )
        out.append(announcement("lapsed", row, "cmp"))

    out.extend(_remove_unmanaged(db))
    return out


def _remove_unmanaged(db: Session) -> list[Announcement]:
    out: list[Announcement] = []
    for entry in catalog.ELEVATIONS.values():
        try:
            found = groups.members(entry.group)
        except groups.GroupError as exc:
            logger.warning("Cannot list %s: %s", entry.group, exc)
            continue
        held = {
            r.requester
            for r in db.query(AccessRequest.requester).filter(
                AccessRequest.elevation == entry.key,
                AccessRequest.status == ACTIVE,
            )
        }
        for username in sorted(found - held):
            removed = settings.ACCESS_REMOVE_UNMANAGED_MEMBERS
            if removed:
                try:
                    groups.remove_member(username, entry.group)
                except groups.GroupError as exc:
                    logger.error("Cannot remove %s: %s", username, exc)
                    removed = False
            record_system_event(
                "access.unmanaged",
                f"{username}:{entry.key}",
                {"group": entry.group, "removed": removed},
                succeeded=removed,
            )
            out.append(
                Announcement(
                    kind="unmanaged",
                    request_id=0,
                    elevation=entry.key,
                    requester=username,
                    actor="cmp",
                    note="removed" if removed else "still a member",
                )
            )
    return out


# ── Announcements ────────────────────────────────────────────────────────────

_COLORS = {
    "requested": 0x2563EB,
    "approved": 0xEA580C,
    "denied": 0x6B7280,
    "cancelled": 0x6B7280,
    "lapsed": 0x6B7280,
    "revoked": 0x059669,
    "expired": 0x059669,
    "unmanaged": 0xDC2626,
}

_TITLES = {
    "requested": "Elevation requested",
    "approved": "Elevation approved",
    "denied": "Elevation denied",
    "cancelled": "Elevation request cancelled",
    "lapsed": "Elevation request lapsed",
    "revoked": "Elevation ended early",
    "expired": "Elevation expired",
    "unmanaged": "Member added outside the CMP",
}


def _embed(a: Announcement) -> dict:
    fields = [
        {"name": "Elevation", "value": a.elevation, "inline": True},
        {"name": "User", "value": a.requester, "inline": True},
        {"name": "By", "value": a.actor, "inline": True},
    ]
    if a.reason and a.kind == "requested":
        fields.append({"name": "Reason", "value": a.reason[:1000]})
    if a.expires_at and a.kind == "approved":
        fields.append(
            {"name": "Expires", "value": f"{a.expires_at.isoformat()}Z"}
        )
    if a.note:
        fields.append({"name": "Note", "value": a.note[:1000]})
    return {
        "title": _TITLES.get(a.kind, a.kind),
        "color": _COLORS.get(a.kind, 0x6B7280),
        "fields": fields,
        "url": f"{settings.CMP_PUBLIC_URL}/access",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "CMP just-in-time access"},
    }


async def _post_discord(a: Announcement) -> None:
    url = settings.SECURITY_DISCORD_WEBHOOK_URL or settings.DISCORD_WEBHOOK_URL
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json={"embeds": [_embed(a)]})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Discord access notice failed: %s", exc)


def _notify(a: Announcement) -> None:
    params = {"user": a.requester, "elevation": a.elevation, "by": a.actor}
    if a.kind == "requested":
        try:
            admins = keycloak_service.list_group_usernames(
                PLATFORM_ADMIN_GROUP
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Approvers unknown, not notified: %s", exc)
            return
        recipients = {u for u in admins if "@" not in u} - {a.requester}
    elif a.kind in ("approved", "denied", "expired", "revoked", "lapsed"):
        if a.actor == a.requester:
            return
        recipients = {a.requester}
    else:
        return
    notifications.notify_users(
        recipients,
        kind="access",
        title_key=f"access_{a.kind}",
        params=params,
        link="/access",
    )


async def announce(items: list[Announcement]) -> None:
    """Never raises: a missed notice must not undo the change."""
    for a in items:
        try:
            await run_in_threadpool(_notify, a)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Access notification failed: %s", exc)
        await _post_discord(a)


async def access_sweep_loop() -> None:
    def _run() -> list[Announcement]:
        with SessionLocal() as db:
            return sweep(db)

    while True:
        try:
            items = await run_in_threadpool(_run)
            await announce(items)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Access sweep error: %s", exc, exc_info=True)
        await asyncio.sleep(SWEEP_SECONDS)
