"""
AI on a project and its hosted agents: what the routes and the MCP tools do.

Every write lands in Git first (the project record through the GitHub App,
the values file of the app repository through a pull request), then in
Keycloak and Vault through the existing services. The ``ai_agents`` table is
a mirror for fast lists; Git stays the source.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai import AiAgent
from app.services import project_registry
from app.services.agents import policy
from app.services.ai import catalog, declaration, gateway, gitops, identity
from app.services.ai import usage as ai_usage
from app.services.ai.declaration import (
    AgentSpec,
    DeclarationError,
    ProjectAi,
    client_id,
)
from app.services.imports.registry_commit import commit_record_change

logger = logging.getLogger(__name__)

PAIR_SUFFIXES = {"reader": "-reader", "actor": "-actor"}


class AiError(Exception):
    """A refusal the API turns into ``status`` with this message."""

    def __init__(self, status: int, message: str, errors: list | None = None):
        self.status = status
        self.errors = errors or []
        super().__init__(message)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def record_of(project: str) -> dict:
    found = await project_registry.read_record_cached(project)
    if found is None:
        raise AiError(404, f"Project '{project}' has no registry record.")
    return found[0]


def _stake(project: str) -> str:
    return policy.project_stake(project).level


async def backend_status() -> dict[str, Any]:
    try:
        budget = await ai_usage.budget()
    except ai_usage.UsageUnavailable:
        budget = {"spend_usd": None, "budget_usd": None, "status": "ok"}
    if budget["status"] == "exhausted":
        status = "spend_limit_reached"
    elif not await gateway.reachable():
        status = "unavailable"
    else:
        status = "ok"
    return {
        "name": "anthropic",
        "kind": "anthropic",
        "status": status,
        "spend_usd": budget["spend_usd"],
        "spend_limit_usd": budget["budget_usd"],
    }


def _mirror_rows(db: Session, project: str) -> dict[str, AiAgent]:
    return {
        row.name: row
        for row in db.query(AiAgent).filter(AiAgent.project == project)
    }


def _summary(
    project: str,
    rights: dict,
    row: AiAgent | None,
    badges: dict[str, list[str]],
    today: dict[str, dict],
) -> dict[str, Any]:
    name = rights["name"]
    declared = (row.declaration if row else None) or {}
    tools = list(rights.get("tools") or [])
    risk = declaration.risk_of(tools, badges).view()
    used = today.get(client_id(project, name))
    return {
        "name": name,
        "project": project,
        "template": declared.get("template")
        or (row.template if row else None),
        "role": row.role if row else None,
        "description": declared.get("description", ""),
        "owner": rights.get("owner"),
        "status": rights.get("status", "active"),
        "source": row.source if row else "yaml",
        "trigger": declared.get("trigger") or {"type": "chat"},
        "model": declared.get("model") or (rights.get("models") or [None])[0],
        "models": list(rights.get("models") or []),
        "tools": tools,
        "isolation": rights.get("isolation") or risk["isolation"],
        "callers": list(rights.get("callers") or []),
        "calls": list(rights.get("calls") or []),
        "daily_token_budget": rights.get("dailyTokenBudget"),
        "client_id": client_id(project, name),
        "repo": row.repo if row else None,
        "values_path": (
            row.values_path if row else declaration.values_path(name)
        ),
        "pull_request": _public_pull(row.pull_request if row else None),
        "risk": risk,
        "usage_today": (
            {
                "tokens": used["tokens_in"] + used["tokens_out"],
                "cost_usd": used["cost_usd"],
                "cost_eur": used["cost_eur"],
            }
            if used
            else None
        ),
        "updated_at": (
            row.updated_at.isoformat() if row and row.updated_at else None
        ),
    }


def _public_pull(pull: dict | None) -> dict | None:
    if not pull:
        return None
    return {k: pull.get(k) for k in ("number", "url", "state")}


async def _today(project: str) -> dict[str, dict]:
    try:
        return {
            item["azp"]: item for item in await ai_usage.usage(project, "1d")
        }
    except ai_usage.UsageUnavailable as exc:
        logger.info("AI usage of %s unavailable: %s", project, exc)
        return {}


def _sync_mirror(db: Session, project: str, project_ai: ProjectAi) -> None:
    """Rights changed in Git without the CMP (or a YAML-first agent):
    the mirror follows the record."""
    rows = _mirror_rows(db, project)
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


async def project_view(db: Session, project: str) -> dict[str, Any]:
    record = await record_of(project)
    project_ai = ProjectAi.of(record)
    stake = await asyncio.to_thread(_stake, project)
    agents = await list_agents(db, project, record=record)
    return {
        "project": project,
        "enabled": project_ai.enabled,
        "can_enable": stake != "critical",
        "stake": stake,
        "models": project_ai.models,
        "available_models": declaration.models(),
        "web_search": project_ai.web_search,
        "daily_token_budget": project_ai.daily_token_budget,
        "apps": [
            {
                "app": app["name"],
                "enabled": app.get("enabled") is True,
                "daily_token_budget": app.get("dailyTokenBudget", 0),
                "client_id": declaration.app_client_id(project, app["name"]),
            }
            for app in project_ai.apps
        ],
        "agents": agents,
        "backend": {
            k: v
            for k, v in (await backend_status()).items()
            if k in ("name", "status")
        },
    }


async def list_agents(
    db: Session, project: str, record: dict | None = None
) -> list[dict]:
    record = record or await record_of(project)
    project_ai = ProjectAi.of(record)
    await asyncio.to_thread(_sync_mirror, db, project, project_ai)
    rows = _mirror_rows(db, project)
    badges = catalog.badges_by_name(await catalog.tools(record))
    today = await _today(project)
    return [
        _summary(project, rights, rows.get(rights["name"]), badges, today)
        for rights in project_ai.agents
    ]


async def get_agent(db: Session, project: str, name: str) -> dict:
    record = await record_of(project)
    project_ai = ProjectAi.of(record)
    rights = project_ai.agent(name)
    if rights is None:
        raise AiError(404, f"Agent '{name}' not found in project '{project}'.")
    await asyncio.to_thread(_sync_mirror, db, project, project_ai)
    row = _mirror_rows(db, project).get(name)
    source = "main"
    declared = None
    repo = row.repo if row else _agent_repo(record, name)
    if repo:
        values = await gitops.read_values(repo, declaration.values_path(name))
        declared = (values or {}).get("agent") if values else None
    if declared is None and row and row.declaration:
        declared, source = row.declaration, "pull_request"
    if row is not None:
        if declared is not None and source == "main":
            row.declaration = declared
        if row.repo is None and repo:
            row.repo = repo
        if row.pull_request and row.pull_request.get("state") == "open":
            row.pull_request = await gitops.pull_state(row.pull_request)
        db.commit()
    badges = catalog.badges_by_name(await catalog.tools(record))
    summary = _summary(project, rights, row, badges, await _today(project))
    return {
        **summary,
        "declaration": declared or {},
        "declaration_source": source if declared else None,
        "capabilities": declaration.capabilities(summary["tools"], badges),
    }


def _agent_repo(record: dict, name: str) -> str | None:
    entry = next(
        (a for a in declaration.record_apps(record) if a.get("name") == name),
        None,
    )
    return catalog.github_repo((entry or {}).get("repoURL"))


# ---------------------------------------------------------------------------
# Project AI
# ---------------------------------------------------------------------------


async def set_project_ai(
    db: Session, project: str, body: dict, username: str
) -> dict:
    stake = await asyncio.to_thread(_stake, project)
    if body["enabled"] and stake == "critical":
        raise AiError(
            409,
            "Un projet critical ne peut pas activer l'IA : les prompts "
            "partent chez Anthropic.",
        )
    record = await record_of(project)
    if (
        body["enabled"]
        and (record.get("spec") or {}).get("targetCloud", "onprem") != "onprem"
    ):
        raise AiError(
            409,
            "L'IA n'est disponible que pour les projets on-prem pour "
            "l'instant.",
        )
    allowed = declaration.models()
    wrong = [m for m in body["models"] if m not in allowed]
    if wrong:
        raise AiError(422, f"Unknown models: {', '.join(wrong)}.")

    async def change(record: dict, _head: str) -> dict:
        record.setdefault("spec", {})["stake"] = stake
        ai = record["spec"].setdefault("ai", {})
        ai["enabled"] = bool(body["enabled"])
        ai["models"] = list(body["models"])
        ai["webSearch"] = bool(body["web_search"])
        ai["dailyTokenBudget"] = int(body["daily_token_budget"])
        return {}

    await _commit(project, change, f"set AI rights, by {username}")
    await gitops.refresh_project(project)
    return await project_view(db, project)


async def refuse_if_ai_enabled(project: str) -> None:
    """A project with AI on cannot become critical: turn AI off first."""
    found = await project_registry.read_record_cached(project)
    if found and ProjectAi.of(found[0]).enabled:
        raise AiError(
            409,
            "L'IA est activée sur ce projet : désactivez-la avant de le "
            "passer en critical.",
        )


async def sync_stake(project: str, stake: str) -> None:
    """``spec.stake`` mirrors the stake level for the record validator."""
    found = await project_registry.read_record_cached(project)
    if found is None or (found[0].get("spec") or {}).get("stake") == stake:
        return

    async def change(record: dict, _head: str) -> dict:
        record.setdefault("spec", {})["stake"] = stake
        return {}

    await _commit(project, change, f"set stake {stake}")


async def _commit(project: str, change, message: str) -> None:
    try:
        await commit_record_change(
            project, change, f"feat(registry): {message}"
        )
    except project_registry.RegistryError as exc:
        raise AiError(502, str(exc)) from exc
    project_registry._records_at_head.pop(  # pylint: disable=protected-access
        project, None
    )


async def enable_app(
    db: Session, project: str, app: str, body: dict, username: str
) -> dict:
    record = await record_of(project)
    project_ai = ProjectAi.of(record)
    entry = next(
        (a for a in declaration.record_apps(record) if a.get("name") == app),
        None,
    )
    if entry is None or entry.get("type") == "agent":
        raise AiError(404, f"App '{app}' not found in project '{project}'.")
    if body["enabled"] and not project_ai.enabled:
        raise AiError(409, f"AI is not enabled on project '{project}'.")
    budget = int(body["daily_token_budget"])
    if (
        project_ai.daily_token_budget
        and budget > project_ai.daily_token_budget
    ):
        raise AiError(
            422,
            f"daily_token_budget {budget} is above the project's "
            f"{project_ai.daily_token_budget}.",
        )
    client = declaration.app_client_id(project, app)
    if body["enabled"]:
        await asyncio.to_thread(
            identity.ensure_client, client, project, f"App {app}", []
        )
        secret = await asyncio.to_thread(identity.client_secret, client)
        await gitops.store_client_secret(db, project, app, client, secret)
    elif await asyncio.to_thread(identity.is_enabled, client):
        await asyncio.to_thread(identity.set_enabled, client, False)

    async def change(record: dict, _head: str) -> dict:
        ai = record.setdefault("spec", {}).setdefault("ai", {})
        apps = [a for a in ai.get("apps") or [] if a.get("name") != app]
        apps.append(
            {
                "name": app,
                "enabled": bool(body["enabled"]),
                "dailyTokenBudget": budget,
            }
        )
        ai["apps"] = sorted(apps, key=lambda a: a["name"])
        return {}

    await _commit(project, change, f"set AI access of {app}")
    pull = None
    repo = catalog.github_repo(entry.get("repoURL"))
    if repo:
        path = catalog.values_file_of(entry)
        current = await gitops.read_values(repo, path)
        if current is not None and (current.get("ai") or {}).get(
            "enabled"
        ) != bool(body["enabled"]):
            block = {
                **(current.get("ai") or {}),
                "enabled": bool(body["enabled"]),
            }
            pull = await _pr(
                repo,
                {path: gitops.values_with(current, "ai", block)},
                f"ai-{app}",
                f"chore(cnp): {'enable' if body['enabled'] else 'disable'} "
                f"AI access of {app}",
                f"The CMP sets `ai.enabled: {str(body['enabled']).lower()}` "
                f"in `{path}`. Rights and budget are in the project record "
                f"({budget} tokens a day). Requested by {username}.",
            )
    await gitops.refresh_project(project)
    return {
        "client_id": client,
        "gateway_url": settings.AI_GATEWAY_URL,
        "secret_ref": gitops.secret_ref(project, app),
        "pull_request": _public_pull(pull),
    }


async def _pr(
    repo: str, files: dict, slug: str, title: str, body: str
) -> dict:
    try:
        return await gitops.open_pr(repo, files, slug, title, body)
    except gitops.GitOpsError as exc:
        raise AiError(502, str(exc)) from exc


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@dataclass
class Planned:
    spec: AgentSpec
    role: str | None


def _merged(defaults: dict, given: dict) -> dict:
    """Template defaults (camelCase, as in values) under the request."""
    fields = {
        "model": defaults.get("model"),
        "models": defaults.get("models"),
        "effort": defaults.get("effort"),
        "max_steps": defaults.get("maxSteps"),
        "trigger": defaults.get("trigger"),
        "instructions": defaults.get("instructions"),
        "tools": defaults.get("tools"),
        "output": defaults.get("output"),
        "description": defaults.get("description"),
        "isolation": defaults.get("isolation"),
        "daily_token_budget": defaults.get("dailyTokenBudget"),
        "calls": defaults.get("calls"),
    }
    fields.update({k: v for k, v in given.items() if v is not None})
    return fields


def _fit_isolation(fields: dict, badges: dict) -> None:
    """An isolation the person did not choose (the template's, or the one
    kept from before new tools) is raised to what the tools need, not
    refused."""
    minimum = declaration.risk_of(fields.get("tools") or [], badges).isolation
    current = fields.get("isolation")
    if current in declaration.ISOLATIONS and declaration.ISOLATIONS.index(
        current
    ) < declaration.ISOLATIONS.index(minimum):
        fields["isolation"] = minimum


async def _plan(
    project: str, body: dict, record: dict, username: str
) -> list[Planned]:
    project_ai = ProjectAi.of(record)
    template = await catalog.template(body["template"])
    if template is None:
        raise AiError(422, f"Unknown agent template '{body['template']}'.")
    badges = catalog.badges_by_name(await catalog.tools(record))
    apps = declaration.record_apps(record)
    existing = {a["name"] for a in project_ai.agents}
    name = body["name"]
    given = {
        k: body.get(k)
        for k in (
            "description",
            "owner",
            "model",
            "effort",
            "max_steps",
            "trigger",
            "instructions",
            "tools",
            "isolation",
            "callers",
            "calls",
            "daily_token_budget",
            "output",
        )
    }
    errors: list[str] = []
    planned: list[Planned] = []
    if template["pair"]:
        reader, actor = (name + PAIR_SUFFIXES[r] for r in ("reader", "actor"))
        known = existing | {reader, actor}
        reader_fields = _merged(template["pair"]["reader"], given)
        reader_fields["calls"] = list(
            dict.fromkeys([*(reader_fields.get("calls") or []), actor])
        )
        actor_given = {
            k: v for k, v in (body.get("actor") or {}).items() if v is not None
        }
        actor_fields = _merged(
            template["pair"]["actor"],
            {
                "owner": given.get("owner"),
                "model": given.get("model"),
                **actor_given,
            },
        )
        actor_fields["trigger"] = {"type": "a2a"}
        actor_fields["callers"] = [f"agent:{reader}"]
        for role, agent, fields, chosen in (
            ("reader", reader, reader_fields, given),
            ("actor", actor, actor_fields, actor_given),
        ):
            fields["template"] = template["id"]
            if chosen.get("isolation") is None:
                _fit_isolation(fields, badges)
            try:
                spec = declaration.build(
                    project,
                    agent,
                    fields,
                    project_ai,
                    badges,
                    apps,
                    username,
                    known,
                )
                planned.append(Planned(spec, role))
            except DeclarationError as exc:
                errors.extend(f"{agent}: {e}" for e in exc.errors)
    else:
        fields = _merged(template["defaults"], given)
        fields["template"] = template["id"]
        if given.get("isolation") is None:
            _fit_isolation(fields, badges)
        try:
            spec = declaration.build(
                project,
                name,
                fields,
                project_ai,
                badges,
                apps,
                username,
                existing | {name},
            )
            planned.append(Planned(spec, None))
        except DeclarationError as exc:
            errors.extend(exc.errors)
    for item in planned:
        if item.spec.name in existing:
            errors.append(f"Agent '{item.spec.name}' already exists.")
    if errors:
        raise AiError(422, "The agent cannot be created.", errors)
    return planned


def _repo_of(record: dict, app: str) -> str:
    entry = next(
        (a for a in declaration.record_apps(record) if a.get("name") == app),
        None,
    )
    if entry is None:
        raise AiError(422, f"App '{app}' not found in the project.")
    repo = catalog.github_repo(entry.get("repoURL"))
    if repo is None:
        raise AiError(
            422,
            f"App '{app}' has no repository in the "
            f"{settings.GITHUB_ORG} organization: its values cannot be "
            "written by a pull request.",
        )
    return repo


def _values_text(spec: AgentSpec) -> str:
    return gitops.values_with({}, "agent", spec.declaration)


def _pr_body(project: str, items: list[Planned], username: str) -> str:
    lines = [
        f"The CMP declares hosted agents of project `{project}`, requested "
        f"by {username}.",
        "",
        "Rights (models, tools, budget, isolation, callers) are already in "
        "the project record; this file can never exceed them. Merging "
        "deploys the agents.",
        "",
    ]
    for item in items:
        lines.append(
            f"- `{declaration.values_path(item.spec.name)}`: "
            f"{item.spec.declaration['description'] or item.spec.name} "
            f"(tools: {', '.join(item.spec.declaration['tools']) or 'none'})"
        )
    return "\n".join(lines)


async def _provision(
    db: Session, project: str, spec: AgentSpec, badges: dict
) -> None:
    agent_client = client_id(project, spec.name)
    await asyncio.to_thread(
        identity.ensure_client,
        agent_client,
        project,
        spec.declaration["description"] or spec.name,
        identity.scopes_for(spec.rights["tools"], badges),
    )
    await asyncio.to_thread(identity.ensure_test_audience, agent_client)
    secret = await asyncio.to_thread(identity.client_secret, agent_client)
    await gitops.store_client_secret(
        db, project, spec.name, agent_client, secret
    )


async def create_agents(
    db: Session, project: str, body: dict, username: str
) -> dict:
    record = await record_of(project)
    repo = _repo_of(record, body["repo_app"])
    planned = await _plan(project, body, record, username)
    for item in planned:
        try:
            await project_registry.ensure_app_names_free(
                project, item.spec.name, "agent"
            )
        except project_registry.NameCollisionError as exc:
            raise AiError(409, str(exc)) from exc
        except project_registry.RegistryError as exc:
            raise AiError(502, str(exc)) from exc
    badges = catalog.badges_by_name(await catalog.tools(record))
    try:
        for item in planned:
            await _provision(db, project, item.spec, badges)
    except identity.IdentityError as exc:
        raise AiError(502, f"Keycloak: {exc}") from exc
    repo_url = f"https://github.com/{repo}"

    async def change(record: dict, _head: str) -> dict:
        spec = record.setdefault("spec", {})
        ai = spec.setdefault("ai", {})
        agents = [
            a
            for a in ai.get("agents") or []
            if a.get("name") not in {p.spec.name for p in planned}
        ]
        agents.extend(p.spec.rights for p in planned)
        ai["agents"] = agents
        apps = spec.setdefault("apps", [])
        for item in planned:
            if not any(a.get("name") == item.spec.name for a in apps):
                apps.append(
                    {
                        "name": item.spec.name,
                        "type": "agent",
                        "repoURL": repo_url,
                    }
                )
        return {}

    names = ", ".join(p.spec.name for p in planned)
    await _commit(project, change, f"declare agents {names}")
    stale = _mirror_rows(db, project)
    for item in planned:
        row = stale.get(item.spec.name) or AiAgent(
            project=project, name=item.spec.name, created_by=username
        )
        row.template = item.spec.declaration["template"]
        row.role = item.role
        row.status = "active"
        row.source = "cmp"
        row.repo = repo
        row.values_path = declaration.values_path(item.spec.name)
        row.rights = item.spec.rights
        row.declaration = item.spec.declaration
        db.add(row)
    db.commit()
    pull = await _pr(
        repo,
        {
            declaration.values_path(p.spec.name): _values_text(p.spec)
            for p in planned
        },
        planned[0].spec.name,
        f"feat(agents): declare {names}",
        _pr_body(project, planned, username),
    )
    for row in _mirror_rows(db, project).values():
        if row.name in {p.spec.name for p in planned}:
            row.pull_request = pull
    db.commit()
    await gitops.refresh_project(project)
    return {
        "agents": [await get_agent(db, project, p.spec.name) for p in planned],
        "pull_request": _public_pull(pull),
    }


DECLARATION_FIELDS = (
    "description",
    "model",
    "effort",
    "max_steps",
    "trigger",
    "instructions",
    "output",
    "tools",
    "calls",
)
RIGHTS_FIELDS = (
    "models",
    "model",
    "tools",
    "isolation",
    "callers",
    "calls",
    "daily_token_budget",
    "owner",
)


async def update_agent(
    db: Session, project: str, name: str, patch: dict, username: str
) -> dict:
    record = await record_of(project)
    project_ai = ProjectAi.of(record)
    rights = project_ai.agent(name)
    if rights is None:
        raise AiError(404, f"Agent '{name}' not found in project '{project}'.")
    current = await get_agent(db, project, name)
    fields = declaration.fields_of(current["declaration"], rights)
    changed = {k: v for k, v in patch.items() if v is not None}
    fields.update(changed)
    badges = catalog.badges_by_name(await catalog.tools(record))
    if "isolation" not in changed:
        _fit_isolation(fields, badges)
    try:
        spec = declaration.build(
            project,
            name,
            fields,
            project_ai,
            badges,
            declaration.record_apps(record),
            rights.get("owner") or username,
            {a["name"] for a in project_ai.agents},
        )
    except DeclarationError as exc:
        raise AiError(422, "The agent cannot be changed.", exc.errors) from exc
    spec.rights["status"] = rights.get("status", "active")
    if spec.rights != rights:

        async def change(record: dict, _head: str) -> dict:
            ai = record["spec"]["ai"]
            ai["agents"] = [
                spec.rights if a.get("name") == name else a
                for a in ai.get("agents") or []
            ]
            return {}

        await _commit(project, change, f"update rights of agent {name}")
        try:
            await asyncio.to_thread(
                identity.ensure_client,
                client_id(project, name),
                project,
                spec.declaration["description"] or name,
                identity.scopes_for(spec.rights["tools"], badges),
            )
        except identity.IdentityError as exc:
            raise AiError(502, f"Keycloak: {exc}") from exc
    row = _mirror_rows(db, project).get(name)
    pull = None
    if spec.declaration != current["declaration"]:
        repo = (row.repo if row else None) or _agent_repo(record, name)
        if repo is None:
            raise AiError(
                409, f"Agent '{name}' has no repository to write to."
            )
        path = declaration.values_path(name)
        values = await gitops.read_values(repo, path)
        pull = await _pr(
            repo,
            {path: gitops.values_with(values, "agent", spec.declaration)},
            name,
            f"feat(agents): update {name}",
            f"The CMP updates the declaration of agent `{name}` "
            f"({', '.join(sorted(changed)) or 'no field'}), requested by "
            f"{username}.",
        )
    if row is not None:
        row.rights = spec.rights
        row.declaration = spec.declaration
        if pull:
            row.pull_request = pull
        db.commit()
    await gitops.refresh_project(project)
    return {
        **await get_agent(db, project, name),
        "pull_request": _public_pull(pull),
    }


async def set_status(
    db: Session, project: str, name: str, status: str, username: str
) -> dict:
    """The kill switch: the record (gateway policy and replicas), the
    Keycloak client (no new token) and an immediate Argo CD refresh."""
    record = await record_of(project)
    if ProjectAi.of(record).agent(name) is None:
        raise AiError(404, f"Agent '{name}' not found in project '{project}'.")
    agent_client = client_id(project, name)
    try:
        if status == "suspended":
            await asyncio.to_thread(identity.set_enabled, agent_client, False)
    except identity.IdentityError as exc:
        if exc.status != 404:
            raise AiError(502, f"Keycloak: {exc}") from exc

    async def change(record: dict, _head: str) -> dict:
        for agent in record["spec"]["ai"].get("agents") or []:
            if agent.get("name") == name:
                agent["status"] = status
        return {}

    await _commit(project, change, f"set agent {name} {status}, by {username}")
    if status == "active":
        try:
            await asyncio.to_thread(identity.set_enabled, agent_client, True)
        except identity.IdentityError as exc:
            raise AiError(502, f"Keycloak: {exc}") from exc
    row = _mirror_rows(db, project).get(name)
    if row is not None:
        row.status = status
        db.commit()
    await gitops.refresh_project(project)
    agents = await list_agents(db, project)
    return next(a for a in agents if a["name"] == name)


async def delete_agent(
    db: Session, project: str, name: str, username: str
) -> dict:
    record = await record_of(project)
    if ProjectAi.of(record).agent(name) is None:
        raise AiError(404, f"Agent '{name}' not found in project '{project}'.")
    row = _mirror_rows(db, project).get(name)
    repo = (row.repo if row else None) or _agent_repo(record, name)
    pull = None
    if repo:
        path = declaration.values_path(name)
        if await gitops.read_values(repo, path) is not None:
            pull = await _pr(
                repo,
                {path: None},
                name,
                f"chore(agents): remove {name}",
                f"The CMP removed agent `{name}` (rights, client, secret); "
                f"this drops its values file. Requested by {username}.",
            )

    async def change(record: dict, _head: str) -> dict:
        spec = record["spec"]
        ai = spec["ai"]
        ai["agents"] = [
            a for a in ai.get("agents") or [] if a.get("name") != name
        ]
        for agent in ai["agents"]:
            if name in (agent.get("calls") or []):
                agent["calls"] = [c for c in agent["calls"] if c != name]
        spec["apps"] = [
            a
            for a in spec.get("apps") or []
            if not (a.get("name") == name and a.get("type") == "agent")
        ]
        return {}

    await _commit(project, change, f"remove agent {name}")
    agent_client = client_id(project, name)
    try:
        await asyncio.to_thread(identity.drop_test_audience, agent_client)
        await asyncio.to_thread(identity.delete_client, agent_client)
    except identity.IdentityError as exc:
        raise AiError(502, f"Keycloak: {exc}") from exc
    await gitops.drop_client_secret(db, project, name)
    if row is not None:
        db.delete(row)
        db.commit()
    await gitops.refresh_project(project)
    return {"deleted": name, "pull_request": _public_pull(pull)}


async def admin_agents(db: Session) -> list[dict]:
    """Every agent of the platform, from the records."""
    items = []
    for record in await project_registry.list_records():
        project = (record.get("metadata") or {}).get("name")
        project_ai = ProjectAi.of(record)
        if not project or not project_ai.agents:
            continue
        await asyncio.to_thread(_sync_mirror, db, project, project_ai)
        rows = _mirror_rows(db, project)
        badges = catalog.badges_by_name(await catalog.tools(record))
        items.extend(
            _summary(project, rights, rows.get(rights["name"]), badges, {})
            for rights in project_ai.agents
        )
    return items
