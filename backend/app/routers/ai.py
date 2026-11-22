"""
AI landing zone: AI on a project, AI access of an app, hosted agents (with
their test chat, usage and traces), the agent draft and the platform views.

The rules live in ``app.services.ai``; this module maps them to HTTP. Reads
need a project member, project AI settings an admin, agents a member to
create or change and an admin to suspend, resume or delete.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.roles import ProjectRole
from app.services import project_registry
from app.services.ai import (
    catalog,
    declaration,
    draft,
    gateway,
    gitops,
    identity,
)
from app.services.ai import registry as ai_registry
from app.services.ai import (
    service,
)
from app.services.ai import usage as ai_usage
from app.services.ai.declaration import ProjectAi, client_id
from app.services.authz import (
    ProjectContext,
    RequireAdmin,
    RequireMember,
    assert_project_role,
    is_cnp_admin,
)
from app.services.github_service import GitHubAppError
from app.services.keycloak_service import get_current_user, security
from app.services.observability.stores import StoreError

projects = APIRouter(prefix="/projects/{project_name}", tags=["ai"])
platform = APIRouter(prefix="/ai", tags=["ai"])
admin = APIRouter(prefix="/admin", tags=["ai"])

CurrentUser = Annotated[dict, Depends(get_current_user)]
Period = Literal["1d", "7d", "30d"]
AGENT = r"^[a-z][a-z0-9-]{0,62}$"
ARTIFACT = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, service.AiError):
        detail: Any = str(exc)
        if exc.errors:
            detail = {"message": str(exc), "errors": exc.errors}
        return HTTPException(status_code=exc.status, detail=detail)
    if isinstance(exc, ai_usage.UsageUnavailable):
        return HTTPException(
            status_code=503, detail="Métriques IA indisponibles."
        )
    if isinstance(exc, gateway.GatewayRefusal):
        return HTTPException(status_code=exc.status, detail=str(exc))
    if isinstance(exc, (gateway.GatewayError, identity.IdentityError)):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, project_registry.RegistryError):
        return HTTPException(status_code=502, detail=str(exc))
    raise exc


FAILURES = (
    service.AiError,
    ai_usage.UsageUnavailable,
    gateway.GatewayRefusal,
    gateway.GatewayError,
    identity.IdentityError,
    project_registry.RegistryError,
)


def _human(ctx: ProjectContext) -> None:
    """Assistants act through the MCP tools, which carry their own rules."""
    if ctx.token.get("cnp_agent"):
        raise HTTPException(
            status_code=403,
            detail="Le chat de test se fait dans la CMP, pas par un assistant.",
        )


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------


class ProjectAiBody(BaseModel):
    enabled: bool
    models: list[str] = Field(default_factory=list)
    web_search: bool = False
    daily_token_budget: int = Field(default=0, ge=0, le=1_000_000_000)


class AppAiBody(BaseModel):
    enabled: bool = True
    # 0 is an app without model access (an MCP server only).
    daily_token_budget: int = Field(default=50_000, ge=0, le=1_000_000_000)


class ReconcileBody(BaseModel):
    # Deleting the clients the record no longer names is never the default:
    # a record edited by mistake must not cut a running agent off.
    prune: bool = False


class Trigger(BaseModel):
    type: Literal["chat", "event", "schedule", "a2a"] = "chat"
    cron: str | None = None
    source: str | None = None


class Output(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    schema_: dict = Field(default_factory=dict, alias="schema")


class ActorBody(BaseModel):
    instructions: str | None = None
    tools: list[str] | None = None
    daily_token_budget: int | None = Field(default=None, ge=0)
    model: str | None = None
    effort: str | None = None
    max_steps: int | None = None


class AgentFields(BaseModel):
    description: str | None = Field(default=None, max_length=300)
    owner: str | None = None
    model: str | None = None
    models: list[str] | None = None
    effort: str | None = None
    max_steps: int | None = None
    trigger: Trigger | None = None
    instructions: str | None = None
    tools: list[str] | None = None
    isolation: Literal["runc", "gvisor", "kata"] | None = None
    callers: list[str] | None = None
    calls: list[str] | None = None
    daily_token_budget: int | None = Field(default=None, ge=0)
    output: Output | None = None

    def fields(self) -> dict:
        data = self.model_dump(exclude_unset=True, by_alias=True)
        if self.trigger is not None:
            data["trigger"] = self.trigger.model_dump(exclude_none=True)
        if self.output is not None:
            data["output"] = {
                "name": self.output.name,
                "schema": self.output.schema_,
            }
        return data


class AgentCreate(AgentFields):
    template: str = Field(min_length=1, max_length=64)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")
    repo_app: str = Field(min_length=1, max_length=63)
    actor: ActorBody | None = None


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class TestBody(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=40)


class DraftBody(BaseModel):
    prompt: str = Field(min_length=3, max_length=draft.MAX_PROMPT)
    project: str | None = None


class McpDecision(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    project: str | None = None
    status: Literal["approved", "retired", "pending"]


# ---------------------------------------------------------------------------
# Project AI
# ---------------------------------------------------------------------------


@projects.get("/ai")
async def get_project_ai(
    ctx: RequireMember, db: Session = Depends(get_db)
) -> dict:
    try:
        return await service.project_view(db, ctx.project_name)
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.put("/ai")
async def put_project_ai(
    body: ProjectAiBody, ctx: RequireAdmin, db: Session = Depends(get_db)
) -> dict:
    try:
        return await service.set_project_ai(
            db, ctx.project_name, body.model_dump(), ctx.username
        )
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.post("/ai/apps/{app}", status_code=201)
async def enable_app_ai(
    app: Annotated[str, Path(pattern=AGENT)],
    body: AppAiBody,
    ctx: RequireAdmin,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await service.enable_app(
            db, ctx.project_name, app, body.model_dump(), ctx.username
        )
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.post("/ai/reconcile")
async def reconcile_project_ai(
    ctx: RequireAdmin,
    body: ReconcileBody | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Clients and secrets of every agent and AI app of the record, made
    to match it; agents declared in Git get theirs here."""
    try:
        return await service.reconcile_ai(
            db, ctx.project_name, (body or ReconcileBody()).prune
        )
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.get("/ai/usage")
async def project_usage(ctx: RequireMember, period: Period = "1d") -> dict:
    try:
        items = await ai_usage.usage(ctx.project_name, period)
        record = await project_registry.read_record_cached(ctx.project_name)
    except FAILURES as exc:
        raise _fail(exc) from exc
    project_ai = ProjectAi.of(record[0]) if record else None
    for item in items:
        rights = None
        if project_ai and item["kind"] == "agent":
            rights = project_ai.agent(item["name"])
        elif project_ai and item["kind"] == "app":
            rights = project_ai.app(item["name"])
        item["budget"] = (rights or {}).get("dailyTokenBudget")
    return {"period": period, "items": items, "totals": ai_usage.totals(items)}


@projects.get("/ai/tools")
async def project_tools(ctx: RequireMember) -> dict:
    try:
        record = await service.record_of(ctx.project_name)
        return {"items": await catalog.tools(record)}
    except FAILURES as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@projects.get("/agents")
async def list_agents(
    ctx: RequireMember, db: Session = Depends(get_db)
) -> dict:
    try:
        return {"items": await service.list_agents(db, ctx.project_name)}
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.post("/agents", status_code=201)
async def create_agent(
    body: AgentCreate, ctx: RequireMember, db: Session = Depends(get_db)
) -> dict:
    payload = body.fields()
    payload.update(
        template=body.template,
        name=body.name,
        repo_app=body.repo_app,
        actor=body.actor.model_dump(exclude_none=True) if body.actor else None,
    )
    try:
        return await service.create_agents(
            db, ctx.project_name, payload, ctx.username
        )
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.get("/agents/{agent}")
async def get_agent(
    agent: Annotated[str, Path(pattern=AGENT)],
    ctx: RequireMember,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await service.get_agent(db, ctx.project_name, agent)
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.patch("/agents/{agent}")
async def update_agent(
    agent: Annotated[str, Path(pattern=AGENT)],
    body: AgentFields,
    ctx: RequireMember,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await service.update_agent(
            db, ctx.project_name, agent, body.fields(), ctx.username
        )
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.delete("/agents/{agent}")
async def delete_agent(
    agent: Annotated[str, Path(pattern=AGENT)],
    ctx: RequireAdmin,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await service.delete_agent(
            db, ctx.project_name, agent, ctx.username
        )
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.post("/agents/{agent}/suspend")
async def suspend_agent(
    agent: Annotated[str, Path(pattern=AGENT)],
    ctx: RequireAdmin,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await service.set_status(
            db, ctx.project_name, agent, "suspended", ctx.username
        )
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.post("/agents/{agent}/resume")
async def resume_agent(
    agent: Annotated[str, Path(pattern=AGENT)],
    ctx: RequireAdmin,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return await service.set_status(
            db, ctx.project_name, agent, "active", ctx.username
        )
    except FAILURES as exc:
        raise _fail(exc) from exc


@projects.get("/agents/{agent}/usage")
async def agent_usage(
    agent: Annotated[str, Path(pattern=AGENT)],
    ctx: RequireMember,
    period: Period = "1d",
) -> dict:
    azp = client_id(ctx.project_name, agent)
    try:
        items = await ai_usage.usage(ctx.project_name, period, azp=azp)
        points = await ai_usage.series(ctx.project_name, azp, period)
        record = await service.record_of(ctx.project_name)
    except FAILURES as exc:
        raise _fail(exc) from exc
    rights = ProjectAi.of(record).agent(agent)
    if rights is None:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent}' not found."
        )
    item = items[0] if items else ai_usage.empty_row(azp, ctx.project_name)
    item["budget"] = rights.get("dailyTokenBudget")
    return {"period": period, **item, "series": points}


@projects.get("/agents/{agent}/traces")
async def agent_traces(
    agent: Annotated[str, Path(pattern=AGENT)],
    ctx: RequireMember,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    try:
        return await ai_registry.agent_traces(ctx.project_name, agent, limit)
    except StoreError as exc:
        raise HTTPException(
            status_code=503, detail="Traces indisponibles."
        ) from exc


async def _test_plan(
    db: Session, project: str, agent: str
) -> gateway.TestPlan:
    detail = await service.get_agent(db, project, agent)
    record = await service.record_of(project)
    project_ai = ProjectAi.of(record)
    if not project_ai.enabled:
        raise service.AiError(409, f"L'IA n'est pas activée sur '{project}'.")
    if detail["status"] != "active":
        raise service.AiError(409, f"L'agent '{agent}' est suspendu.")
    declared = detail["declaration"]
    model = declared.get("model") or detail["model"]
    return gateway.TestPlan(
        project=project,
        agent=agent,
        model=model,
        effort=declared.get("effort") or declaration.default_effort(model),
        instructions=declared.get("instructions") or detail["description"],
        tools=list(detail["tools"]),
        max_steps=min(
            int(declared.get("maxSteps") or settings.AI_TEST_MAX_STEPS),
            settings.AI_TEST_MAX_STEPS,
        ),
        output=declared.get("output"),
        artifact_base=f"/api/projects/{project}/agents/{agent}/artifacts",
    )


def _frame(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n".encode()


@projects.post("/agents/{agent}/test")
async def test_agent(
    agent: Annotated[str, Path(pattern=AGENT)],
    body: TestBody,
    ctx: RequireMember,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Server-sent events, one per step of the agent's loop."""
    _human(ctx)
    try:
        plan = await _test_plan(db, ctx.project_name, agent)
        token = await asyncio.to_thread(
            identity.exchange_for_agent,
            credentials.credentials,
            client_id(ctx.project_name, agent),
        )
    except FAILURES as exc:
        raise _fail(exc) from exc
    rate = await asyncio.to_thread(ai_usage.usd_per_eur)

    async def frames() -> AsyncIterator[bytes]:
        async for event, payload in gateway.run_test(
            plan,
            token,
            body.message,
            [h.model_dump() for h in body.history],
            rate,
        ):
            if event == "done":
                payload["trace_url"] = ai_registry.grafana_url(
                    ctx.project_name, agent, payload["trace_id"]
                )
            yield _frame(event, payload)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@projects.get("/agents/{agent}/artifacts/{path:path}")
async def get_artifact(
    agent: Annotated[str, Path(pattern=AGENT)],
    path: str,
    ctx: RequireMember,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> Response:
    """A file the agent produced, fetched from the gateway as the user."""
    _human(ctx)
    if not declaration.NAME.match(agent) or not _artifact_ok(path):
        raise HTTPException(status_code=404, detail="Unknown artifact.")
    try:
        token = await asyncio.to_thread(
            identity.exchange_for_agent,
            credentials.credentials,
            client_id(ctx.project_name, agent),
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.get(
                f"{settings.AI_GATEWAY_URL}/ai-artifacts/{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
    except identity.IdentityError as exc:
        raise _fail(exc) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Gateway unreachable."
        ) from exc
    if upstream.status_code != 200:
        raise HTTPException(status_code=404, detail="Unknown artifact.")
    return Response(
        upstream.content,
        media_type=upstream.headers.get(
            "content-type", "application/octet-stream"
        ),
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _artifact_ok(path: str) -> bool:
    return bool(re.match(ARTIFACT, path)) and ".." not in path


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


@platform.get("/backends")
async def backends(_token: CurrentUser) -> dict:
    return {"items": [await service.backend_status()]}


@platform.get("/budget")
async def budget(_token: CurrentUser) -> dict:
    try:
        return await ai_usage.budget()
    except FAILURES as exc:
        raise _fail(exc) from exc


@platform.get("/templates")
async def templates(_token: CurrentUser) -> dict:
    try:
        return {"items": await catalog.templates()}
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@platform.post("/draft-agent")
async def draft_agent(
    body: DraftBody, token: CurrentUser, db: Session = Depends(get_db)
) -> dict:
    record = None
    project = body.project or "draft"
    if body.project:
        await asyncio.to_thread(
            assert_project_role, db, token, body.project, ProjectRole.MEMBER
        )
        try:
            record = await service.record_of(body.project)
        except FAILURES as exc:
            raise _fail(exc) from exc
    try:
        return await draft.draft(body.prompt, record, project)
    except FAILURES as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Platform admin
# ---------------------------------------------------------------------------


def require_platform_admin(token: CurrentUser) -> dict:
    if not is_cnp_admin(token):
        raise HTTPException(
            status_code=403, detail="Réservé aux admins de la plateforme."
        )
    return token


PlatformAdmin = Annotated[dict, Depends(require_platform_admin)]


@admin.get("/agents")
async def admin_agents(
    _token: PlatformAdmin, db: Session = Depends(get_db)
) -> dict:
    try:
        return {"items": await service.admin_agents(db)}
    except FAILURES as exc:
        raise _fail(exc) from exc


@admin.get("/mcp-servers")
async def admin_mcp_servers(_token: PlatformAdmin) -> dict:
    try:
        return {"items": await ai_registry.mcp_servers()}
    except FAILURES as exc:
        raise _fail(exc) from exc


@admin.post("/mcp-servers", status_code=202)
async def decide_mcp_server(body: McpDecision, token: PlatformAdmin) -> dict:
    try:
        pull = await ai_registry.decide_server(
            body.name,
            body.project,
            body.status,
            token.get("preferred_username", ""),
        )
    except gitops.GitOpsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"pull_request": {k: pull[k] for k in ("number", "url", "state")}}
