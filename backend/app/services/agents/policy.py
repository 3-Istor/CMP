"""
How freely an assistant acts in a project, from what is at stake in it.

The rules are data (``policy.yaml``) evaluated here, server side, before any
tool touches the platform. Tool annotations and client-side approvals help
the user see what is going on; they are not what keeps a project safe.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from urllib.parse import quote

import httpx
from ruamel.yaml import YAML

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.agent import AgentConfirmation, ProjectAgentSettings

LEVELS = ("sandbox", "demo", "standard", "critical")
DEFAULT_LEVEL = "standard"
DECISIONS = ("allow", "ask", "pr", "confirm", "deny")
CONFIRMATION_TTL = timedelta(minutes=30)
# Tools whose confirmation is a draft a person reviews and applies on its own
# page in the CMP, with a passkey: approving it in /connect/confirm, or
# calling the tool again, never runs it.
PORTAL_TOOLS = frozenset({"import_repo"})
MAX_AUTONOMOUS = timedelta(hours=8)


@cache
def rules() -> dict:
    data = YAML(typ="safe").load(Path(__file__).with_name("policy.yaml"))
    for action, row in data["actions"].items():
        for level in LEVELS:
            if row.get(level) not in DECISIONS:
                raise ValueError(f"policy.yaml: {action}.{level} is invalid")
    return data


@dataclass
class ProjectStake:
    project: str
    level: str
    autonomous_until: datetime | None

    @property
    def effective_level(self) -> str:
        if (
            self.level == "standard"
            and self.autonomous_until is not None
            and self.autonomous_until > datetime.now(timezone.utc)
        ):
            return rules()["autonomous_as"]
        return self.level


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=timezone.utc)


def project_stake(project: str) -> ProjectStake:
    with SessionLocal() as db:
        row = db.get(ProjectAgentSettings, project)
        if row is None:
            return ProjectStake(project, DEFAULT_LEVEL, None)
        return ProjectStake(project, row.level, _aware(row.autonomous_until))


def set_project_stake(
    project: str,
    username: str,
    level: str | None = None,
    autonomous_hours: float | None = None,
) -> ProjectStake:
    """``autonomous_hours=0`` closes an open autonomous window."""
    if level is not None and level not in LEVELS:
        raise ValueError(f"Unknown level {level!r}.")
    with SessionLocal() as db:
        row = db.get(ProjectAgentSettings, project)
        if row is None:
            row = ProjectAgentSettings(
                project_name=project, level=DEFAULT_LEVEL
            )
            db.add(row)
        if level is not None:
            row.level = level
        if autonomous_hours is not None:
            hours = timedelta(hours=autonomous_hours)
            row.autonomous_until = (
                datetime.now(timezone.utc) + min(hours, MAX_AUTONOMOUS)
                if autonomous_hours > 0
                else None
            )
        row.updated_by = username
        db.commit()
        return ProjectStake(project, row.level, _aware(row.autonomous_until))


@dataclass
class Decision:
    action: str
    level: str
    outcome: str
    reason: str


def decide(
    action: str, stake: ProjectStake, incident: bool = False
) -> Decision:
    data = rules()
    level = stake.effective_level
    outcome = data["actions"][action][level]
    reason = f"{action} on a {level} project"
    if level != stake.level:
        reason += " (autonomous window open)"
    if incident and action in data.get("incident", {}):
        relaxed = data["incident"][action]
        if DECISIONS.index(relaxed) < DECISIONS.index(outcome):
            outcome = relaxed
            reason += ", relaxed: an alert is firing on the app"
    return Decision(action, level, outcome, reason)


def sandbox_max_apps() -> int:
    return int(rules()["limits"]["sandbox_max_apps"])


async def app_has_firing_alert(namespace: str | None) -> bool:
    """An unsilenced alert firing in the app's namespace."""
    if not namespace:
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(
                f"{settings.ALERTMANAGER_URL}/api/v2/alerts",
                params={
                    "active": "true",
                    "silenced": "false",
                    "filter": f'namespace="{namespace}"',
                },
            )
    except httpx.HTTPError:
        return False
    return response.status_code == 200 and bool(response.json())


# ---------------------------------------------------------------------------
# Confirmations (decision "confirm")
# ---------------------------------------------------------------------------


@dataclass
class ConfirmationView:
    id: str
    username: str
    project: str
    deployment_id: int | None
    tool: str
    arguments: dict
    summary: str
    status: str
    created_at: datetime
    expires_at: datetime
    decided_by: str | None


def _confirmation_view(row: AgentConfirmation) -> ConfirmationView:
    status = row.status
    if status == "pending" and _aware(row.expires_at) < datetime.now(
        timezone.utc
    ):
        status = "expired"
    return ConfirmationView(
        id=row.id,
        username=row.username,
        project=row.project,
        deployment_id=row.deployment_id,
        tool=row.tool,
        arguments=dict(row.arguments),
        summary=row.summary,
        status=status,
        created_at=_aware(row.created_at),
        expires_at=_aware(row.expires_at),
        decided_by=row.decided_by,
    )


def request_confirmation(
    grant_id: int,
    username: str,
    project: str,
    deployment_id: int | None,
    tool: str,
    arguments: dict,
    summary: str,
) -> ConfirmationView:
    with SessionLocal() as db:
        row = AgentConfirmation(
            id=secrets.token_urlsafe(12),
            grant_id=grant_id,
            username=username,
            project=project,
            deployment_id=deployment_id,
            tool=tool,
            arguments=arguments,
            summary=summary,
            status="pending",
            expires_at=datetime.now(timezone.utc) + CONFIRMATION_TTL,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _confirmation_view(row)


def load_confirmation(confirmation_id: str) -> ConfirmationView | None:
    with SessionLocal() as db:
        row = db.get(AgentConfirmation, confirmation_id)
        return _confirmation_view(row) if row else None


def decide_confirmation(
    confirmation_id: str, approved: bool, username: str
) -> ConfirmationView | None:
    with SessionLocal() as db:
        row = db.get(AgentConfirmation, confirmation_id)
        if row is None:
            return None
        if _confirmation_view(row).status == "pending":
            row.status = "approved" if approved else "denied"
            row.decided_by = username
            row.decided_at = datetime.now(timezone.utc)
            db.commit()
        return _confirmation_view(row)


def draft_review_link(view: ConfirmationView) -> str:
    """The CMP page where a person reviews and applies an import draft."""
    return (
        f"/projects/{quote(view.project)}/import?run="
        f"{quote(str(view.arguments.get('run_id', '')))}&draft={view.id}"
    )


def settle_draft(
    confirmation_id: str, project: str, source_id: str, username: str
) -> bool:
    """
    Mark the assistant's draft of an import used once a person applied the
    import in the CMP. False when it is not a pending draft of this import.
    """
    with SessionLocal() as db:
        row = db.get(AgentConfirmation, confirmation_id)
        if (
            row is None
            or row.tool not in PORTAL_TOOLS
            or row.project != project
            or dict(row.arguments).get("source_id") != source_id
            or _confirmation_view(row).status != "pending"
        ):
            return False
        row.status = "used"
        row.decided_by = username
        row.decided_at = datetime.now(timezone.utc)
        db.commit()
        return True


def consume_confirmation(
    confirmation_id: str, grant_id: int, tool: str, arguments: dict
) -> ConfirmationView | None:
    """
    The approved confirmation for exactly this call, marked used so it cannot
    authorize a second one. ``None`` when there is no such approval.
    """
    with SessionLocal() as db:
        row = db.get(AgentConfirmation, confirmation_id)
        if (
            row is None
            or row.grant_id != grant_id
            or row.tool != tool
            or dict(row.arguments) != arguments
            or row.status != "approved"
        ):
            return None
        row.status = "used"
        db.commit()
        return _confirmation_view(row)
