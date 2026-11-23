"""
The settings of each agent, read from ``deploy/values-<agent>.yaml`` (key
``agent:``) in the repository of its app, and whether Keycloak has its
client, kept in the ``ai_agents`` mirror.

The lists only read the mirror; the agent page, a reconcile and the
periodic sync write it. A file that is missing or unreadable is shown as
such: nothing is made up in its place.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from ruamel.yaml import YAMLError
from sqlalchemy.orm import Session

from app.models.ai import AiAgent
from app.services.ai import catalog, declaration, gitops
from app.services.ai.declaration import ProjectAi
from app.services.github_service import GitHubAppError

logger = logging.getLogger(__name__)

OK = "ok"
MISSING = "missing"
UNREADABLE = "unreadable"
NOT_FOUND_MESSAGE = "Réglages introuvables dans le dépôt"


def mirror_rows(db: Session, project: str) -> dict[str, AiAgent]:
    return {
        row.name: row
        for row in db.query(AiAgent).filter(AiAgent.project == project)
    }


def sync_rows(db: Session, project: str, project_ai: ProjectAi) -> None:
    """Rights changed in Git without the CMP (or a YAML-first agent):
    the mirror follows the record."""
    rows = mirror_rows(db, project)
    for rights in project_ai.agents:
        row = rows.get(rights["name"])
        if row is None:
            row = AiAgent(
                project=project,
                name=rights["name"],
                source="yaml",
                declaration={},
                created_by=rights.get("owner") or "",
            )
            db.add(row)
        row.rights = rights
        row.status = rights.get("status", "active")
    known = {a["name"] for a in project_ai.agents}
    for name, row in rows.items():
        if name not in known:
            db.delete(row)
    db.commit()


def agent_repo(record: dict, name: str) -> str | None:
    """The repository of the agent's app (``type: agent``) in the record."""
    entry = next(
        (a for a in declaration.record_apps(record) if a.get("name") == name),
        None,
    )
    return catalog.github_repo((entry or {}).get("repoURL"))


async def read(row: AiAgent, record: dict) -> None:
    """Reads the values file of *row* on main into the row (not committed).
    An unreadable file keeps the last good settings; a missing one keeps
    only the declaration of a pull request still open."""
    repo = row.repo or agent_repo(record, row.name)
    path = declaration.values_path(row.name)
    row.repo = repo
    row.values_path = path
    row.settings_read_at = datetime.now(timezone.utc)
    if repo is None:
        _missing(row)
        return
    try:
        values = await gitops.read_values(repo, path)
    except (GitHubAppError, httpx.HTTPError, YAMLError) as exc:
        logger.info("Settings of agent %s unreadable: %s", row.name, exc)
        row.settings_status = UNREADABLE
        return
    block = (values or {}).get("agent")
    if not isinstance(block, dict):
        _missing(row)
        return
    row.declaration = block
    row.template = block.get("template")
    row.settings_status = OK


def _missing(row: AiAgent) -> None:
    row.settings_status = MISSING
    if not (row.pull_request and row.pull_request.get("state") == "open"):
        row.declaration = {}
        row.template = None


async def refresh(
    db: Session, project: str, record: dict, clients: dict | None = None
) -> None:
    """Every agent of the project; with *clients* (Keycloak's, by client
    id) also whether each one has its credentials."""
    await asyncio.to_thread(sync_rows, db, project, ProjectAi.of(record))
    rows = list(mirror_rows(db, project).values())
    await asyncio.gather(*(read(row, record) for row in rows))
    if clients is not None:
        for row in rows:
            known = declaration.client_id(project, row.name) in clients
            row.credentials = "ok" if known else "missing"
    await asyncio.to_thread(db.commit)


def needs_refresh(rows: dict[str, AiAgent], project_ai: ProjectAi) -> bool:
    """An agent of the record the mirror has not read yet."""
    return any(
        (row := rows.get(a["name"])) is None or row.settings_read_at is None
        for a in project_ai.agents
    )


def view(row: AiAgent | None) -> dict:
    status = row.settings_status if row else None
    has_settings = bool(row and row.declaration)
    return {
        "status": status,
        "message": (
            NOT_FOUND_MESSAGE
            if status in (MISSING, UNREADABLE) and not has_settings
            else None
        ),
        "read_at": (
            row.settings_read_at.isoformat()
            if row and row.settings_read_at
            else None
        ),
    }
