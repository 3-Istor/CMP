"""Discord and notification centre messages of the access review."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.services import keycloak_service, notifications
from app.services.authz import PLATFORM_ADMIN_GROUP

logger = logging.getLogger(__name__)

KIND = "access_review"

_TITLES = {
    "opened": "Access review opened",
    "escalated": "Access review overdue",
    "terraform_task": "Platform group removal to apply in Terraform",
    "inactive": "Inactive account",
    "disabled": "Inactive account disabled",
}

_COLORS = {
    "opened": 0x2563EB,
    "escalated": 0xDC2626,
    "terraform_task": 0xEA580C,
    "inactive": 0xEA580C,
    "disabled": 0xDC2626,
}


@dataclass(frozen=True)
class Inbox:
    """One notification centre message, to *users* and/or every infra."""

    title_key: str
    params: dict
    link: str
    users: frozenset[str] = frozenset()
    project: str | None = None
    to_infra: bool = False


@dataclass(frozen=True)
class Notice:
    kind: str
    fields: tuple[tuple[str, str], ...]
    link: str
    inbox: tuple[Inbox, ...] = field(default_factory=tuple)


def _infra() -> set[str]:
    try:
        members = keycloak_service.list_group_usernames(PLATFORM_ADMIN_GROUP)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Platform admins unknown, not notified: %s", exc)
        return set()
    return {u for u in members if "@" not in u}


def _store(notice: Notice) -> None:
    infra: set[str] | None = None
    for box in notice.inbox:
        users = set(box.users)
        if box.to_infra:
            if infra is None:
                infra = _infra()
            users |= infra
        notifications.notify_users(
            users,
            kind=KIND,
            title_key=box.title_key,
            params=box.params,
            link=box.link,
            project=box.project,
        )


def _embed(notice: Notice) -> dict:
    return {
        "title": _TITLES.get(notice.kind, notice.kind),
        "color": _COLORS.get(notice.kind, 0x6B7280),
        "fields": [
            {"name": name, "value": value[:1000], "inline": len(value) < 40}
            for name, value in notice.fields
        ],
        "url": f"{settings.CMP_PUBLIC_URL}{notice.link}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "CMP access review"},
    }


async def _post_discord(notice: Notice) -> None:
    url = settings.SECURITY_DISCORD_WEBHOOK_URL or settings.DISCORD_WEBHOOK_URL
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url, json={"embeds": [_embed(notice)]}
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Discord access review notice failed: %s", exc)


async def deliver(items: list[Notice]) -> None:
    """Never raises: a missed notice must not undo the change."""
    for notice in items:
        try:
            await run_in_threadpool(_store, notice)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Access review notification failed: %s", exc)
        await _post_discord(notice)
