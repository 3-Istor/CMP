"""
MCP tools for AI on a project and its hosted agents (guide ``cnp-ai``).

Same rules as the other tools: the CMP API is called with the user's token,
so it keeps checking roles; the tool checks the same role first and
``_project_gate`` adds the stake policy (``ai.configure``, ``agent.create``,
``agent.update``, ``agent.suspend``, ``agent.delete``). The test chat stays
in the CMP: it streams and runs as the person, not as an assistant.
"""

from typing import Annotated, Any, Literal

from pydantic import Field

from app.core.roles import ProjectRole
from app.mcp_operations import DESTRUCTIVE, WRITE, _project_gate
from app.mcp_server import (
    PREVIEW,
    PUBLIC_URL,
    READ_ONLY,
    Caller,
    mcp,
    tool_errors,
)
from app.services import audit
from app.services.ai.usage import (
    APP_DAILY_TOKEN_BUDGET,
    PROJECT_DAILY_TOKEN_CAP,
)

AGENT_FIELDS = (
    "description",
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
)


def _agent_link(project: str, agent: str) -> str:
    return f"{PUBLIC_URL}/projects/{project}/agents/{agent}"


# ---------------------------------------------------------------------------
# Project AI
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def get_project_ai(project: str) -> dict:
    """AI on a project: enabled or not, allowed models, budgets, the apps
    and agents with AI access and the state of the model backend. Read the
    guide cnp-ai (get_guide) before creating an agent."""
    caller = Caller()
    await caller.require_role(project, ProjectRole.MEMBER)
    return await caller.api("GET", f"/api/projects/{project}/ai")


@mcp.tool(annotations=WRITE)
@tool_errors
async def set_project_ai(  # pylint: disable=too-many-arguments
    project: str,
    enabled: bool,
    models: Annotated[
        list[str],
        Field(description="Allowed models, e.g. claude-haiku-5-5"),
    ],
    web_search: bool = False,
    daily_token_budget: Annotated[int, Field(ge=0)] = PROJECT_DAILY_TOKEN_CAP,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
    preview: PREVIEW = False,
) -> dict:
    """Turn AI on or off for a project, with its allowed models, web search
    and daily token budget (an alert threshold for the project). A critical
    project cannot turn AI on. Project admins only."""
    caller = Caller()
    body = {
        "enabled": enabled,
        "models": models,
        "web_search": web_search,
        "daily_token_budget": daily_token_budget,
    }
    current = (
        await caller.api("GET", f"/api/projects/{project}/ai")
        if preview
        else None
    )
    stop = await _project_gate(
        caller,
        project,
        "ai.configure",
        minimum=ProjectRole.ADMIN,
        tool="set_project_ai",
        arguments={"project": project, **body},
        summary=f"{'Enable' if enabled else 'Disable'} AI on '{project}' "
        f"(models {', '.join(models) or 'none'}, {daily_token_budget} tokens "
        "a day)?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
        preview=preview,
        changes=(
            audit.changes({k: (current or {}).get(k) for k in body}, body)
            if preview
            else None
        ),
    )
    if stop:
        return stop
    result = await caller.api("PUT", f"/api/projects/{project}/ai", json=body)
    return {"ok": True, "ai": result}


@mcp.tool(annotations=WRITE)
@tool_errors
async def enable_app_ai(  # pylint: disable=too-many-arguments
    project: str,
    app: Annotated[str, Field(description="App name in the project")],
    enabled: bool = True,
    daily_token_budget: Annotated[int, Field(ge=0)] = APP_DAILY_TOKEN_BUDGET,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
    preview: PREVIEW = False,
) -> dict:
    """Give an app access to the models through the AI gateway: its own
    Keycloak client, a daily token cap in the project record and a pull
    request setting ai.enabled in its values. Project admins only."""
    caller = Caller()
    body = {"enabled": enabled, "daily_token_budget": daily_token_budget}
    stop = await _project_gate(
        caller,
        project,
        "ai.configure",
        minimum=ProjectRole.ADMIN,
        tool="enable_app_ai",
        arguments={"project": project, "app": app, **body},
        summary=f"{'Give' if enabled else 'Remove'} AI access "
        f"{'to' if enabled else 'from'} app '{app}' of '{project}' "
        f"({daily_token_budget} tokens a day)?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
        preview=preview,
    )
    if stop:
        return stop
    result = await caller.api(
        "POST", f"/api/projects/{project}/ai/apps/{app}", json=body
    )
    return {"ok": True, **result}


@mcp.tool(annotations=WRITE)
@tool_errors
async def reconcile_project_ai(  # pylint: disable=too-many-arguments
    project: str,
    prune: Annotated[
        bool,
        Field(
            description="true also deletes the agent and app clients the "
            "project record no longer names"
        ),
    ] = False,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
    preview: PREVIEW = False,
) -> dict:
    """Make the Keycloak clients and Vault secrets of every agent and AI
    app match the project record: what was declared in Git gets its
    credentials, a suspended agent its client disabled. Running it again
    changes nothing. Orphan clients are listed, deleted only with prune.
    Project admins only."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "ai.configure",
        minimum=ProjectRole.ADMIN,
        tool="reconcile_project_ai",
        arguments={"project": project, "prune": prune},
        summary=f"Provision the AI credentials of '{project}' from its "
        f"record{' and delete orphan clients' if prune else ''}?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
        preview=preview,
    )
    if stop:
        return stop
    result = await caller.api(
        "POST", f"/api/projects/{project}/ai/reconcile", json={"prune": prune}
    )
    return {"ok": True, **result}


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def get_ai_usage(
    project: str,
    period: Literal["1d", "7d", "30d"] = "1d",
    agent: Annotated[
        str | None, Field(description="One agent only, with its series")
    ] = None,
) -> dict:
    """Tokens, cost at the public price, errors and refusals (budget,
    policy, rate limit, guardrail), per app or agent of a project."""
    caller = Caller()
    await caller.require_role(project, ProjectRole.MEMBER)
    path = (
        f"/api/projects/{project}/agents/{agent}/usage"
        if agent
        else f"/api/projects/{project}/ai/usage"
    )
    return await caller.api("GET", path, params={"period": period})


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def list_agent_templates() -> dict:
    """The agent templates (assistant, docs-qa, data-analyst, app-bot,
    ops-diagnosis, pr-review) with their defaults and risk notes."""
    caller = Caller()
    return await caller.api("GET", "/api/ai/templates")


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def list_agents(project: str) -> dict:
    """The hosted agents of a project, declared in the CMP or in YAML: one
    list, with status, tools, risk and today's usage."""
    caller = Caller()
    await caller.require_role(project, ProjectRole.MEMBER)
    return await caller.api("GET", f"/api/projects/{project}/agents")


@mcp.tool(annotations=READ_ONLY)
@tool_errors
async def get_agent(project: str, agent: str) -> dict:
    """One agent: its declaration (the values file), rights, capability map
    (what it reads, does, sends out, runs) and its pull request."""
    caller = Caller()
    await caller.require_role(project, ProjectRole.MEMBER)
    return await caller.api("GET", f"/api/projects/{project}/agents/{agent}")


@mcp.tool(annotations=WRITE)
@tool_errors
async def create_agent(  # pylint: disable=too-many-arguments,too-many-locals
    project: str,
    template: Annotated[str, Field(description="Template id")],
    name: Annotated[
        str,
        Field(description="Lowercase, dashes; app-bot adds -reader/-actor"),
    ],
    repo_app: Annotated[
        str,
        Field(
            description="The project app whose repository receives "
            "deploy/values-<agent>.yaml by pull request"
        ),
    ],
    instructions: str | None = None,
    tools: Annotated[
        list[str] | None,
        Field(description="Tool names from get_project_ai / the template"),
    ] = None,
    description: str | None = None,
    model: str | None = None,
    trigger: Annotated[
        dict | None,
        Field(description='e.g. {"type": "schedule", "cron": "0 8 * * *"}'),
    ] = None,
    daily_token_budget: Annotated[int | None, Field(ge=0)] = None,
    actor: Annotated[
        dict | None,
        Field(description="app-bot only: {instructions, tools} of the actor"),
    ] = None,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
    preview: PREVIEW = False,
) -> dict:
    """Declare a hosted agent from a template: its rights in the project
    record, its Keycloak client, and a pull request with its values file.
    Refused when its tools combine private data, untrusted content and
    sending out (Rule of Two): use app-bot to split it. Read the guide
    cnp-ai (get_guide) first."""
    caller = Caller()
    body: dict[str, Any] = {
        "template": template,
        "name": name,
        "repo_app": repo_app,
        "instructions": instructions,
        "tools": tools,
        "description": description,
        "model": model,
        "trigger": trigger,
        "daily_token_budget": daily_token_budget,
        "actor": actor,
    }
    body = {k: v for k, v in body.items() if v is not None}
    stop = await _project_gate(
        caller,
        project,
        "agent.create",
        minimum=ProjectRole.MEMBER,
        tool="create_agent",
        arguments={"project": project, **body},
        summary=f"Create agent '{name}' ({template}) in '{project}' with "
        f"tools {', '.join(tools or []) or 'of the template'}?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
        preview=preview,
    )
    if stop:
        return stop
    created = await caller.api(
        "POST", f"/api/projects/{project}/agents", json=body
    )
    return {
        "ok": True,
        **created,
        "links": [
            _agent_link(project, a.get("name", ""))
            for a in created.get("agents") or []
        ],
        "next": "A person merges the pull request; the platform then "
        "deploys the agent. Test it in the CMP (links).",
    }


@mcp.tool(annotations=WRITE)
@tool_errors
async def update_agent(  # pylint: disable=too-many-arguments
    project: str,
    agent: str,
    changes: Annotated[
        dict[str, Any],
        Field(
            description="Fields to change: "
            + ", ".join(AGENT_FIELDS)
            + ". Read get_agent first."
        ),
    ],
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
    preview: PREVIEW = False,
) -> dict:
    """Change an agent: rights in the project record at once, declaration
    by a pull request on its values file."""
    unknown = sorted(set(changes) - set(AGENT_FIELDS))
    if not changes or unknown:
        raise ValueError(
            f"Give fields among {', '.join(AGENT_FIELDS)}"
            + (f"; unknown: {', '.join(unknown)}" if unknown else "")
        )
    caller = Caller()
    current = (
        await caller.api("GET", f"/api/projects/{project}/agents/{agent}")
        if preview
        else None
    )
    stop = await _project_gate(
        caller,
        project,
        "agent.update",
        minimum=ProjectRole.MEMBER,
        tool="update_agent",
        arguments={"project": project, "agent": agent, "changes": changes},
        summary=f"Change {', '.join(sorted(changes))} of agent '{agent}'?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
        preview=preview,
        changes=(
            audit.changes(
                {k: (current or {}).get(k) for k in changes}, changes
            )
            if preview
            else None
        ),
    )
    if stop:
        return stop
    result = await caller.api(
        "PATCH", f"/api/projects/{project}/agents/{agent}", json=changes
    )
    return {"ok": True, "agent": result}


@mcp.tool(annotations=WRITE)
@tool_errors
async def suspend_agent(  # pylint: disable=too-many-arguments
    project: str,
    agent: str,
    resume: Annotated[
        bool, Field(description="true reactivates a suspended agent")
    ] = False,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
    preview: PREVIEW = False,
) -> dict:
    """The kill switch: suspend an agent (no new token, gateway policy
    closed, scaled to zero; issued tokens die within 5 minutes), or resume
    it. Project admins only."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "agent.update" if resume else "agent.suspend",
        minimum=ProjectRole.ADMIN,
        tool="suspend_agent",
        arguments={"project": project, "agent": agent, "resume": resume},
        summary=f"{'Resume' if resume else 'Suspend'} agent '{agent}' of "
        f"'{project}'?",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
        preview=preview,
    )
    if stop:
        return stop
    result = await caller.api(
        "POST",
        f"/api/projects/{project}/agents/{agent}/"
        f"{'resume' if resume else 'suspend'}",
    )
    return {"ok": True, "agent": result}


@mcp.tool(annotations=DESTRUCTIVE)
@tool_errors
async def delete_agent(  # pylint: disable=too-many-arguments
    project: str,
    agent: str,
    user_confirmed: bool = False,
    confirmation_id: str | None = None,
    preview: PREVIEW = False,
) -> dict:
    """Delete an agent: its rights, Keycloak client and secret go at once,
    its values file by a pull request. Project admins only."""
    caller = Caller()
    stop = await _project_gate(
        caller,
        project,
        "agent.delete",
        minimum=ProjectRole.ADMIN,
        tool="delete_agent",
        arguments={"project": project, "agent": agent},
        summary=f"Delete agent '{agent}' of '{project}'? Its client and "
        "secret are removed for good.",
        user_confirmed=user_confirmed,
        confirmation_id=confirmation_id,
        preview=preview,
    )
    if stop:
        return stop
    result = await caller.api(
        "DELETE", f"/api/projects/{project}/agents/{agent}"
    )
    return {"ok": True, **(result or {})}
