"""
Discord alerts on new core findings.

Only the core tier is sent: it caps the grade and must be fixed now. A
finding is sent once; if it disappears and comes back within
RENOTIFY_AFTER, as during a Trivy rescan, it is not sent again.

Developer findings go to the app's webhook, else the project's, else the
platform default. Platform findings only go to the platform default, since
the team cannot act on them. Team webhooks are secrets (anyone holding one
can post), so they live in the project's Vault mount, under system/ where
the project's own policy only reads.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.security import SecurityFinding
from app.services.security import store
from app.services.security.model import (
    CATEGORY_LABELS,
    Audience,
    Category,
    Tier,
)
from app.services.vault_client import read_kv, write_kv

logger = logging.getLogger(__name__)

RENOTIFY_AFTER = timedelta(days=7)
VAULT_PATH = "system/security-alerts"
PROJECT_KEY = "project"
WEBHOOK_PATTERN = re.compile(
    r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$"
)
EMBEDS_PER_MESSAGE = 10
REQUEST_TIMEOUT = 10.0
RED = 0xDC2626


def _mount(project: str) -> str:
    return f"project-{project}"


def _app_key(app: str) -> str:
    return f"app:{app}"


def platform_default() -> str:
    return (
        settings.SECURITY_DISCORD_WEBHOOK_URL or settings.DISCORD_WEBHOOK_URL
    )


def is_discord_webhook(url: str) -> bool:
    return bool(WEBHOOK_PATTERN.match(url))


def hint(url: str) -> str:
    """Enough to recognise a webhook without disclosing its token."""
    webhook_id = url.rstrip("/").split("/")[-2]
    return f"webhook …{webhook_id[-4:]}"


async def read_targets(project: str) -> dict[str, str]:
    return await read_kv(_mount(project), VAULT_PATH)


async def write_target(project: str, app: str | None, url: str | None) -> None:
    targets = await read_targets(project)
    key = _app_key(app) if app else PROJECT_KEY
    if url:
        targets[key] = url
    else:
        targets.pop(key, None)
    await write_kv(_mount(project), VAULT_PATH, targets)


def resolve(
    targets: dict[str, str], app: str | None, audience: Audience
) -> tuple[str, str] | None:
    """(webhook, where it comes from), or None when nothing is set."""
    if audience is Audience.DEVELOPER:
        if app and targets.get(_app_key(app)):
            return targets[_app_key(app)], "app"
        if targets.get(PROJECT_KEY):
            return targets[PROJECT_KEY], "project"
    default = platform_default()
    return (default, "platform") if default else None


def to_notify(
    db: Session, project: str, fresh: list[SecurityFinding], now: datetime
) -> list[SecurityFinding]:
    """
    The fresh findings worth a message, one per app and fingerprint. Every
    row of a sent finding is marked, so a second source does not resend it.
    """
    exceptions = store.active_exceptions(db, project)
    picked: dict[tuple[str | None, str], SecurityFinding] = {}
    for row in fresh:
        if row.tier != Tier.CORE.value:
            continue
        if row.notified_at and now - row.notified_at < RENOTIFY_AFTER:
            continue
        exception = exceptions.get(row.fingerprint)
        if exception and store.exception_applies(
            exception, Tier.CORE, now.date()
        ):
            continue
        picked.setdefault((row.app, row.fingerprint), row)
    for row in fresh:
        if (row.app, row.fingerprint) in picked:
            row.notified_at = now
    return list(picked.values())


def _embed(project: str, finding: SecurityFinding) -> dict:
    app_query = f"&app={finding.app}" if finding.app else ""
    fields = [
        {"name": "App", "value": finding.app or "projet", "inline": True},
        {
            "name": "Catégorie",
            "value": CATEGORY_LABELS[Category(finding.category)],
            "inline": True,
        },
    ]
    if finding.location:
        fields.append(
            {"name": "Où", "value": finding.location[:1000], "inline": False}
        )
    if finding.fix:
        fields.append(
            {"name": "Correctif", "value": finding.fix[:1000], "inline": False}
        )
    return {
        "title": f"Nouveau problème du socle : {finding.title}"[:256],
        "description": finding.detail[:2000],
        "url": (
            f"{settings.CMP_PUBLIC_URL}/security?project={project}{app_query}"
        ),
        "color": RED,
        "fields": fields,
        "footer": {"text": f"CMP Sécurité · {project}"},
    }


async def post(webhook: str, embeds: list[dict], content: str = "") -> None:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for start in range(0, len(embeds) or 1, EMBEDS_PER_MESSAGE):
            response = await client.post(
                webhook,
                json={
                    "content": content,
                    "embeds": embeds[start : start + EMBEDS_PER_MESSAGE],
                },
            )
            response.raise_for_status()


async def send(project: str, findings: list[SecurityFinding]) -> None:
    """Best effort: a failed alert is logged, the finding stays on the page."""
    if not findings:
        return
    try:
        targets = await read_targets(project)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Security: team webhooks of '%s' unreadable: %s", project, exc
        )
        targets = {}
    batches: dict[str, list[dict]] = defaultdict(list)
    for finding in findings:
        target = resolve(targets, finding.app, Audience(finding.audience))
        if target:
            batches[target[0]].append(_embed(project, finding))
    for webhook, embeds in batches.items():
        try:
            await post(webhook, embeds)
        except httpx.HTTPError as exc:
            logger.warning(
                "Security: Discord alert for '%s' failed: %s", project, exc
            )
