"""Every AI route: who may call it (401, 403) and what it hands the service."""

from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.routers import ai as ai_router
from app.services import keycloak_service
from app.services.agents import policy
from app.services.ai import catalog, draft, identity, registry, service
from app.services.ai import usage as ai_usage
from app.services.github_service import GitHubAppError

ROLES = {("admin1", "alpha"): "admin", ("member1", "alpha"): "member"}
USERS = {
    "admin1": {},
    "member1": {},
    "stranger": {},
    "infra": {"groups": ["/infra"]},
}
AGENT_PATH = "/api/projects/alpha/agents/helper"
REAL_PROJECT_VIEW = service.project_view
REAL_TOOLS = catalog.tools

# (method, path, json body, minimum: member | admin | platform | user)
ROUTES = [
    ("GET", "/api/projects/alpha/ai", None, "member"),
    (
        "PUT",
        "/api/projects/alpha/ai",
        {"enabled": True, "models": ["claude-haiku-5-5"]},
        "admin",
    ),
    ("POST", "/api/projects/alpha/ai/apps/shop", {"enabled": True}, "admin"),
    ("POST", "/api/projects/alpha/ai/reconcile", {"prune": False}, "admin"),
    ("GET", "/api/projects/alpha/ai/usage?period=7d", None, "member"),
    ("GET", "/api/projects/alpha/ai/tools", None, "member"),
    ("GET", "/api/projects/alpha/agents", None, "member"),
    (
        "POST",
        "/api/projects/alpha/agents",
        {"template": "assistant", "name": "helper", "repo_app": "shop"},
        "member",
    ),
    ("GET", AGENT_PATH, None, "member"),
    ("PATCH", AGENT_PATH, {"instructions": "Be brief."}, "member"),
    ("DELETE", AGENT_PATH, None, "admin"),
    ("POST", f"{AGENT_PATH}/suspend", None, "admin"),
    ("POST", f"{AGENT_PATH}/resume", None, "admin"),
    ("GET", f"{AGENT_PATH}/usage", None, "member"),
    ("GET", f"{AGENT_PATH}/traces", None, "member"),
    ("POST", f"{AGENT_PATH}/test", {"message": "Bonjour"}, "member"),
    ("GET", "/api/ai/backends", None, "user"),
    ("GET", "/api/ai/budget", None, "user"),
    ("GET", "/api/ai/templates", None, "user"),
    ("POST", "/api/ai/draft-agent", {"prompt": "Un bot qui résume"}, "user"),
    ("GET", "/api/admin/agents", None, "platform"),
    ("GET", "/api/admin/mcp-servers", None, "platform"),
    (
        "POST",
        "/api/admin/mcp-servers",
        {"name": "shop", "project": "alpha", "status": "approved"},
        "platform",
    ),
]
ALLOWED = {
    "user": {"admin1", "member1", "stranger", "infra"},
    "member": {"admin1", "member1", "infra"},
    "admin": {"admin1", "infra"},
    "platform": {"infra"},
}
DETAIL = {
    "name": "helper",
    "status": "active",
    "model": "claude-haiku-5-5",
    "description": "",
    "tools": [],
    "declaration": {"instructions": "Hi", "maxSteps": 2},
}


def _claims(token: str) -> dict:
    if token not in USERS:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"sub": token, "preferred_username": token, **USERS[token]}


async def _events(*_args, **_kwargs):
    yield "done", {"stop_reason": "end_turn", "trace_id": "ab" * 16}


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)

    def db():
        with session() as s:
            yield s

    monkeypatch.setattr(keycloak_service, "decode_platform_token", _claims)
    monkeypatch.setattr(
        keycloak_service,
        "get_project_role",
        lambda user, project: ROLES.get((user, project)),
    )
    ok = mock.AsyncMock(return_value={"ok": True})
    for name in (
        "project_view",
        "set_project_ai",
        "enable_app",
        "reconcile_ai",
        "create_agents",
        "get_agent",
        "update_agent",
        "delete_agent",
        "set_status",
        "backend_status",
    ):
        monkeypatch.setattr(service, name, ok)
    monkeypatch.setattr(
        service, "list_agents", mock.AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        service, "admin_agents", mock.AsyncMock(return_value=[])
    )
    record = {
        "spec": {"ai": {"enabled": True, "agents": [{"name": "helper"}]}}
    }
    monkeypatch.setattr(
        service, "record_of", mock.AsyncMock(return_value=record)
    )
    monkeypatch.setattr(
        ai_router.project_registry,
        "read_record_cached",
        mock.AsyncMock(return_value=(record, "sha")),
    )
    monkeypatch.setattr(ai_usage, "usage", mock.AsyncMock(return_value=[]))
    monkeypatch.setattr(
        ai_usage, "today_by_azp", mock.AsyncMock(return_value={})
    )
    monkeypatch.setattr(ai_usage, "series", mock.AsyncMock(return_value=[]))
    monkeypatch.setattr(ai_usage, "budget", mock.AsyncMock(return_value={}))
    monkeypatch.setattr(ai_usage, "usd_per_eur", lambda: 1.1)
    monkeypatch.setattr(
        ai_router.catalog, "tools", mock.AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        ai_router.catalog, "templates", mock.AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        registry, "agent_traces", mock.AsyncMock(return_value={"items": []})
    )
    monkeypatch.setattr(
        registry, "mcp_servers", mock.AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        registry,
        "decide_server",
        mock.AsyncMock(
            return_value={"number": 1, "url": "u", "state": "open"}
        ),
    )
    monkeypatch.setattr(
        draft, "draft", mock.AsyncMock(return_value={"ok": True})
    )
    monkeypatch.setattr(
        ai_router.service, "get_agent", mock.AsyncMock(return_value=DETAIL)
    )
    monkeypatch.setattr(identity, "exchange_for_agent", lambda *_: "gw-token")
    monkeypatch.setattr(ai_router.gateway, "run_test", _events)

    app = FastAPI()
    app.include_router(ai_router.projects, prefix="/api")
    app.include_router(ai_router.platform, prefix="/api")
    app.include_router(ai_router.admin, prefix="/api")
    app.dependency_overrides[get_db] = db
    return TestClient(app)


def _call(client, method, path, body, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("method, path, body, _minimum", ROUTES)
def test_a_call_without_a_token_is_refused_with_401(
    client, method, path, body, _minimum
):
    response = _call(client, method, path, body)

    assert response.status_code == 401


@pytest.mark.parametrize("method, path, body, _minimum", ROUTES)
def test_a_call_with_an_invalid_token_is_refused_with_401(
    client, method, path, body, _minimum
):
    response = _call(client, method, path, body, token="forged")

    assert response.status_code == 401


@pytest.mark.parametrize("user", sorted(USERS))
@pytest.mark.parametrize("method, path, body, minimum", ROUTES)
def test_each_route_admits_exactly_the_roles_it_needs(
    client, method, path, body, minimum, user
):
    response = _call(client, method, path, body, token=user)

    if user in ALLOWED[minimum]:
        assert response.status_code < 400, response.text
    else:
        assert response.status_code == 403


def test_the_test_chat_streams_server_sent_events(client):
    response = _call(
        client, "POST", f"{AGENT_PATH}/test", {"message": "Bonjour"}, "member1"
    )

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("event: done\ndata: {")
    assert '"trace_url": "https://grafana.3istor.com/explore' in response.text


def test_the_test_chat_of_a_suspended_agent_is_refused_before_streaming(
    client, monkeypatch
):
    monkeypatch.setattr(
        ai_router.service,
        "get_agent",
        mock.AsyncMock(return_value={**DETAIL, "status": "suspended"}),
    )

    response = _call(
        client, "POST", f"{AGENT_PATH}/test", {"message": "Bonjour"}, "member1"
    )

    assert response.status_code == 409


def test_an_assistant_token_cannot_open_a_test_chat(client, monkeypatch):
    USERS["assistant"] = {"cnp_agent": {"grant_id": 1}}
    ROLES[("assistant", "alpha")] = "member"
    try:
        response = _call(
            client,
            "POST",
            f"{AGENT_PATH}/test",
            {"message": "Bonjour"},
            "assistant",
        )
    finally:
        del USERS["assistant"], ROLES[("assistant", "alpha")]

    assert response.status_code == 403


def test_declaration_errors_come_back_as_a_list(client, monkeypatch):
    monkeypatch.setattr(
        service,
        "create_agents",
        mock.AsyncMock(
            side_effect=service.AiError(422, "Refused", ["Rule of Two"])
        ),
    )

    response = _call(
        client,
        "POST",
        "/api/projects/alpha/agents",
        {"template": "assistant", "name": "helper", "repo_app": "shop"},
        "member1",
    )

    assert response.json()["detail"] == {
        "message": "Refused",
        "errors": ["Rule of Two"],
    }


def test_an_agent_name_with_a_path_in_it_is_refused(client):
    response = _call(
        client, "GET", "/api/projects/alpha/agents/..%2Fx", None, "member1"
    )

    assert response.status_code in (404, 422)


def test_usage_carries_each_callers_budget_from_the_record(
    client, monkeypatch
):
    monkeypatch.setattr(
        ai_usage,
        "usage",
        mock.AsyncMock(
            return_value=[
                {
                    **ai_usage.empty_row("agent-alpha-helper", "alpha"),
                    "cost_usd": 0.5,
                }
            ]
        ),
    )
    record = {
        "spec": {
            "ai": {"agents": [{"name": "helper", "dailyTokenBudget": 900}]}
        }
    }
    monkeypatch.setattr(
        ai_router.project_registry,
        "read_record_cached",
        mock.AsyncMock(return_value=(record, "sha")),
    )

    body = _call(
        client, "GET", "/api/projects/alpha/ai/usage", None, "member1"
    ).json()

    assert (body["items"][0]["budget"], body["totals"]["cost_usd"]) == (
        900,
        0.5,
    )


def test_the_project_ai_degrades_when_the_github_token_cannot_be_had(
    client, monkeypatch
):
    record = {
        "metadata": {"name": "alpha"},
        "spec": {
            "apps": [
                {
                    "name": "shop",
                    "type": "service",
                    "repoURL": "https://github.com/3-Istor/shop",
                }
            ],
            "ai": {"enabled": True, "agents": [{"name": "helper"}]},
        },
    }
    monkeypatch.setattr(service, "project_view", REAL_PROJECT_VIEW)
    monkeypatch.setattr(
        service, "record_of", mock.AsyncMock(return_value=record)
    )
    monkeypatch.setattr(catalog, "tools", REAL_TOOLS)
    monkeypatch.setattr(catalog, "_cache", {})
    monkeypatch.setattr(
        catalog,
        "get_org_installation_token",
        mock.AsyncMock(side_effect=GitHubAppError("Vault is sealed")),
    )
    monkeypatch.setattr(
        policy,
        "project_stake",
        lambda project: policy.ProjectStake(project, "standard", None),
    )

    response = _call(client, "GET", "/api/projects/alpha/ai", None, "member1")

    assert (response.status_code, response.json()["degraded"]) == (
        200,
        ["github"],
    )


def test_the_agent_list_carries_a_server_timing_header(client):
    response = _call(
        client, "GET", "/api/projects/alpha/agents", None, "member1"
    )

    assert "total;dur=" in response.headers["Server-Timing"]
