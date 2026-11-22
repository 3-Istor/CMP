"""
The AI identities of a project brought to what its record says.

Git is the source: an agent declared only in YAML (``spec.ai.agents`` and
its ``type: agent`` app) or an app given ``spec.ai.apps[].enabled`` by hand
gets the same Keycloak client and Vault secret as one made in the CMP. A
suspended agent keeps its client, disabled; an app with AI off keeps its
client closed and never gets one. Clients of the project the record no
longer names are orphans: reported always, deleted only when asked.

Runs after each record write of the CMP, on a timer for the writes made in
Git, and on demand (route and MCP tool). Every step reads before it writes,
so a run over a project already in order writes nothing.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import project_registry, vault_client
from app.services.ai import catalog, gitops, identity
from app.services.ai.declaration import (
    ProjectAi,
    app_client_id,
    client_id,
    record_apps,
)

logger = logging.getLogger(__name__)

CREDENTIALS_TTL = 30.0


@dataclass
class Wanted:
    kind: str
    name: str
    client: str
    scopes: list[str]
    enabled: bool
    allowed: bool


class _Runs:
    loop: asyncio.AbstractEventLoop | None = None
    locks: dict[str, asyncio.Lock] = {}
    credentials: dict[str, tuple[float, dict[str, dict]]] = {}


_runs = _Runs()


def has_ai(record: dict) -> bool:
    project_ai = ProjectAi.of(record)
    return bool(project_ai.agents or project_ai.apps)


def wanted(
    project: str, project_ai: ProjectAi, badges: dict[str, list[str]]
) -> list[Wanted]:
    items = [
        Wanted(
            "agent",
            rights["name"],
            client_id(project, rights["name"]),
            identity.scopes_for(list(rights.get("tools") or []), badges),
            rights.get("status", "active") != "suspended",
            True,
        )
        for rights in project_ai.agents
    ]
    items.extend(
        Wanted(
            "app",
            app["name"],
            app_client_id(project, app["name"]),
            [],
            app.get("enabled") is True,
            app.get("enabled") is True,
        )
        for app in project_ai.apps
    )
    return items


async def _one(db: Session, project: str, item: Wanted) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": item.kind,
        "name": item.name,
        "client_id": item.client,
        "enabled": item.enabled,
        "changes": [],
        "error": None,
    }
    try:
        if not item.allowed:
            if await asyncio.to_thread(identity.is_enabled, item.client):
                await asyncio.to_thread(
                    identity.set_enabled, item.client, False
                )
                entry["changes"].append("disabled")
            return entry
        entry["changes"] = await asyncio.to_thread(
            identity.reconcile_client,
            item.client,
            project,
            f"{'Agent' if item.kind == 'agent' else 'App'} {item.name}",
            item.scopes,
            item.enabled,
            item.kind == "agent",
        )
        secret = await asyncio.to_thread(identity.client_secret, item.client)
        if not await gitops.client_secret_in_place(
            db, project, item.name, item.client, secret
        ):
            await gitops.store_client_secret(
                db, project, item.name, item.client, secret
            )
            entry["changes"].append("secret_written")
    except (identity.IdentityError, vault_client.VaultError) as exc:
        logger.warning("AI reconcile of %s failed: %s", item.client, exc)
        entry["error"] = str(exc)
    return entry


async def _drop(
    db: Session, project: str, client: str, app_names: set[str]
) -> None:
    """An orphan agent takes its Vault role along unless an app of the
    record still has that name; an orphan app client only its secret, the
    app's Vault role serving its other secrets."""
    agent = client.startswith(f"agent-{project}-")
    name = client.removeprefix(f"agent-{project}-").removeprefix(
        f"app-{project}-"
    )
    if agent:
        await asyncio.to_thread(identity.drop_test_audience, client)
    await asyncio.to_thread(identity.delete_client, client)
    if agent and name not in app_names:
        await gitops.drop_client_secret(db, project, name)
    else:
        await vault_client.delete_kv(
            gitops.secret_mount(project), gitops.secret_path(name)
        )


def _lock(project: str) -> asyncio.Lock:
    return _runs.locks.setdefault(project, asyncio.Lock())


async def reconcile_project(
    db: Session, project: str, record: dict, prune: bool = False
) -> dict[str, Any]:
    """
    Raises:
        IdentityError: Keycloak could not list the project's clients.
        GitHubAppError: The tool catalogue could not be read.
    """
    async with _lock(project):
        project_ai = ProjectAi.of(record)
        badges = catalog.badges_by_name(await catalog.tools(record))
        items = [
            await _one(db, project, item)
            for item in wanted(project, project_ai, badges)
        ]
        known = {i["client_id"] for i in items}
        clients = await asyncio.to_thread(identity.project_clients, project)
        orphans = sorted(
            c
            for c, about in clients.items()
            if c not in known and about["project"] == project
        )
        deleted = []
        if prune:
            names = {a.get("name") for a in record_apps(record)}
            for client in orphans:
                await _drop(db, project, client, names)
                deleted.append(client)
        _runs.credentials.pop(project, None)
    changed = bool(deleted) or any(i["changes"] for i in items)
    if changed:
        logger.info(
            "AI reconcile of %s: %s, deleted %s",
            project,
            {i["client_id"]: i["changes"] for i in items if i["changes"]},
            deleted,
        )
    return {
        "project": project,
        "changed": changed,
        "items": items,
        "orphans": [c for c in orphans if c not in deleted],
        "deleted": deleted,
        "errors": sum(1 for i in items if i["error"]),
    }


async def credentials(project: str) -> dict[str, dict] | None:
    """The project's clients for the agent lists, None when Keycloak cannot
    say. Cached briefly: the lists are polled."""
    cached = _runs.credentials.get(project)
    if cached and time.monotonic() < cached[0]:
        return cached[1]
    try:
        found = await asyncio.to_thread(identity.project_clients, project)
    except identity.IdentityError as exc:
        logger.info("Clients of %s unreadable: %s", project, exc)
        return None
    _runs.credentials[project] = (time.monotonic() + CREDENTIALS_TTL, found)
    return found


async def _quietly(project: str, record: dict | None = None) -> None:
    try:
        if record is None:
            found = await project_registry.read_record_cached(project)
            record = found[0] if found else None
        if record is None or not has_ai(record):
            return
        with SessionLocal() as db:
            await reconcile_project(db, project, record)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("AI reconcile of %s failed", project)


def request(project: str) -> None:
    """After a record write: reconcile soon, on the app's loop; safe from
    any thread, a no-op before the loop started."""
    loop = _runs.loop
    if loop is None or loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(_quietly(project), loop)


async def reconcile_loop() -> None:
    """Every project with agents or AI apps, for what was written in Git."""
    _runs.loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(settings.AI_RECONCILE_INTERVAL_SECONDS)
        try:
            records = await project_registry.list_records()
        except project_registry.RegistryError as exc:
            logger.warning("AI reconcile skipped: %s", exc)
            continue
        for record in records:
            project = (record.get("metadata") or {}).get("name")
            if project and has_ai(record):
                await _quietly(project, record)
