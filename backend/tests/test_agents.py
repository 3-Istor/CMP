import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.agent  # noqa: F401
import app.models.audit  # noqa: F401
import app.models.deployment  # noqa: F401
from app import mcp_server
from app.core.config import settings
from app.core.database import Base, get_db
from app.models.project import TargetCloud
from app.routers import agents as agents_router
from app.services import audit, keycloak_service
from app.services.agents import (
    internal_api,
    policy,
    ratelimit,
    repo,
    skills,
    store,
)
from app.services.keycloak_service import (
    decode_platform_token,
    get_current_user,
    security,
)

HUMAN = {"sub": "user-1", "preferred_username": "alice", "name": "Alice"}
INFRA = {"sub": "user-9", "preferred_username": "ivan", "groups": ["infra"]}
REDIRECT = "http://localhost:4567/callback"


@pytest.fixture
def platform():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    api = FastAPI()
    api.include_router(agents_router.router, prefix="/api")
    api.include_router(agents_router.projects_router, prefix="/api")

    @api.get("/api/projects/")
    def projects(token: dict = Depends(get_current_user)):
        return [{"name": "demo", "role": "owner", "status": "active"}]

    promoted = []

    @api.get("/api/deployments/{deployment_id}")
    def deployment(
        deployment_id: int, token: dict = Depends(get_current_user)
    ):
        return {
            "id": deployment_id,
            "name": "shop",
            "project_id": "demo",
            "status": "running",
            "k8s_namespace": None,
        }

    @api.post("/api/deployments/{deployment_id}/release/promote")
    def promote(deployment_id: int, token: dict = Depends(get_current_user)):
        promoted.append(token["preferred_username"])
        return {"strategy": "canary", "phase": "Progressing", "steps": []}

    def current_user(credentials=Depends(security)):
        token = credentials.credentials
        if token.startswith(store.ACCESS_PREFIX):
            return decode_platform_token(token)
        return INFRA if token == "infra" else HUMAN

    def db():
        with factory() as session:
            yield session

    api.dependency_overrides[get_current_user] = current_user
    api.dependency_overrides[get_db] = db
    api.mount("/", mcp_server.mcp.streamable_http_app())
    internal_api.bind(api)

    patches = [
        mock.patch.object(store, "SessionLocal", factory),
        mock.patch.object(policy, "SessionLocal", factory),
        mock.patch.object(
            keycloak_service,
            "get_user_by_id",
            lambda sub: {"id": sub, "enabled": True},
        ),
        mock.patch.object(
            skills,
            "list_skills",
            mock.AsyncMock(
                return_value=[skills.Skill("cnp-platform", "Start here", [])]
            ),
        ),
        mock.patch.object(
            agents_router, "assert_project_role", lambda *a, **k: None
        ),
    ]
    for patch in patches:
        patch.start()
    # The session manager runs once per instance, and runs in the app's
    # lifespan: a fresh one per test.
    mcp_server.mcp._session_manager = None  # pylint: disable=protected-access
    mcp_server.mcp.streamable_http_app()
    api.router.routes.pop()
    api.mount("/", mcp_server.mcp.streamable_http_app())

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with mcp_server.mcp.session_manager.run():
            yield

    api.router.lifespan_context = lifespan
    with TestClient(api, base_url="http://localhost") as client:
        client.promoted = promoted
        yield client
    for patch in patches:
        patch.stop()


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _register(client, redirect=REDIRECT):
    return client.post(
        "/register",
        json={
            "redirect_uris": [redirect],
            "client_name": "Claude Code",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )


def _connect(client, scopes=None) -> dict:
    client_id = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    authorize = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
        follow_redirects=False,
    )
    consent = urlparse(authorize.headers["location"])
    request_id = parse_qs(consent.query)["request"][0]
    approved = client.post(
        f"/api/agents/requests/{request_id}/approve",
        json={"scopes": scopes or store.SCOPES},
        headers={"Authorization": "Bearer human"},
    )
    back = urlparse(approved.json()["redirect_url"])
    code = parse_qs(back.query)["code"][0]
    tokens = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
        },
    ).json()
    return {"client_id": client_id, **tokens}


def _result(response) -> dict:
    return json.loads(response.json()["result"]["content"][0]["text"])


def _as_a_tool(client, token: str, method: str, path: str, **kwargs) -> int:
    """
    Status of an API call made the way MCP tools make it, in-process: the
    only way an assistant token reaches the REST API.
    """
    try:
        client.portal.call(
            lambda: internal_api.call(token, method, path, **kwargs)
        )
    except internal_api.ApiError as exc:
        return exc.status_code
    return 200


def _call(client, token: str, tool: str, arguments: dict | None = None):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        },
    )


def test_registration_refuses_an_unknown_redirect_uri(platform):
    response = _register(platform, "https://evil.example/callback")

    assert response.status_code == 400


def test_authorize_sends_the_user_to_the_cmp_consent_page(platform):
    client_id = _register(platform).json()["client_id"]
    _, challenge = _pkce()

    response = platform.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )

    assert "/connect/authorize?request=" in response.headers["location"]


def test_user_can_grant_write_even_when_the_client_asked_for_read(platform):
    client_id = _register(platform).json()["client_id"]
    verifier, challenge = _pkce()
    authorize = platform.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": store.READ_SCOPE,
        },
        follow_redirects=False,
    )
    request_id = parse_qs(urlparse(authorize.headers["location"]).query)[
        "request"
    ][0]
    approved = platform.post(
        f"/api/agents/requests/{request_id}/approve",
        json={"scopes": store.SCOPES},
        headers={"Authorization": "Bearer human"},
    )
    code = parse_qs(urlparse(approved.json()["redirect_url"]).query)["code"]
    tokens = platform.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code[0],
            "code_verifier": verifier,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
        },
    ).json()

    assert tokens["scope"] == "cnp:read cnp:write"


def test_approved_connection_yields_tokens_carrying_the_user(platform):
    tokens = _connect(platform)

    claims = store.token_claims(tokens["access_token"])

    assert (claims["sub"], claims["cnp_agent"]["client"]) == (
        "user-1",
        "Claude Code",
    )


def test_tool_call_acts_as_the_connected_user(platform):
    tokens = _connect(platform)

    content = _result(_call(platform, tokens["access_token"], "whoami"))

    assert (content["user"], content["projects"][0]["stake"]) == (
        "alice",
        "standard",
    )


def test_mcp_without_token_points_to_the_protected_resource_metadata(
    platform,
):
    response = platform.post("/mcp", json={})

    assert "resource_metadata" in response.headers["www-authenticate"]


def test_refresh_token_works_once(platform):
    tokens = _connect(platform)
    body = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": tokens["client_id"],
    }

    first = platform.post("/token", data=body)
    second = platform.post("/token", data=body)

    assert (first.status_code, second.status_code) == (200, 400)


def test_revoked_assistant_is_refused_on_its_next_call(platform):
    tokens = _connect(platform)
    grant = platform.get(
        "/api/agents/grants", headers={"Authorization": "Bearer human"}
    ).json()[0]
    platform.delete(
        f"/api/agents/grants/{grant['id']}",
        headers={"Authorization": "Bearer human"},
    )

    response = _call(platform, tokens["access_token"], "whoami")

    assert response.status_code == 401


def _refresh(client, tokens):
    return client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": tokens["client_id"],
        },
    )


def _live_grants(client) -> list[dict]:
    return client.get(
        "/api/agents/admin/grants", headers={"Authorization": "Bearer infra"}
    ).json()


def test_refresh_revokes_the_grant_of_a_disabled_account(
    platform, monkeypatch
):
    # Arrange
    tokens = _connect(platform)
    monkeypatch.setattr(
        keycloak_service,
        "get_user_by_id",
        lambda sub: {"id": sub, "enabled": False},
    )

    # Act
    issued = store.rotate_refresh(
        tokens["refresh_token"], tokens["client_id"], []
    )

    # Assert
    assert issued is None
    assert _live_grants(platform) == []


def test_refresh_revokes_the_grant_of_a_deleted_account(platform, monkeypatch):
    tokens = _connect(platform)
    monkeypatch.setattr(keycloak_service, "get_user_by_id", lambda sub: None)

    response = _refresh(platform, tokens)

    assert response.json()["error"] == "invalid_grant"
    assert store.token_claims(tokens["access_token"]) is None


def test_refresh_is_refused_but_the_grant_kept_when_keycloak_is_down(
    platform, monkeypatch
):
    # Arrange
    tokens = _connect(platform)

    def down(sub):
        raise keycloak_service.requests.ConnectionError("down")

    monkeypatch.setattr(keycloak_service, "get_user_by_id", down)

    # Act
    issued = store.rotate_refresh(
        tokens["refresh_token"], tokens["client_id"], []
    )

    # Assert
    assert issued is None
    assert len(_live_grants(platform)) == 1


def test_refresh_works_again_once_keycloak_is_back(platform, monkeypatch):
    tokens = _connect(platform)
    monkeypatch.setattr(
        keycloak_service,
        "get_user_by_id",
        mock.Mock(side_effect=keycloak_service.requests.Timeout("slow")),
    )
    _refresh(platform, tokens)
    monkeypatch.setattr(
        keycloak_service,
        "get_user_by_id",
        lambda sub: {"id": sub, "enabled": True},
    )

    response = _refresh(platform, tokens)

    assert response.status_code == 200


def _keycloak_answers(monkeypatch, status_code):
    response = keycloak_service.requests.Response()
    response.status_code = status_code
    monkeypatch.setattr(keycloak_service, "_get_admin_token", lambda: "t")
    monkeypatch.setattr(
        keycloak_service.requests, "get", lambda *a, **k: response
    )


def test_account_lookup_reads_a_404_as_gone(monkeypatch):
    _keycloak_answers(monkeypatch, 404)

    user = keycloak_service.get_user_by_id("user-1")

    assert user is None


@pytest.mark.parametrize("status_code", [500, 401])
def test_account_lookup_raises_when_keycloak_cannot_say(
    monkeypatch, status_code
):
    _keycloak_answers(monkeypatch, status_code)

    with pytest.raises(keycloak_service.requests.HTTPError):
        keycloak_service.get_user_by_id("user-1")


def test_disabling_an_account_revokes_all_its_grants(platform):
    tokens = _connect(platform)
    _connect(platform)

    revoked = store.revoke_subject(HUMAN["sub"])

    assert len(revoked) == 2
    assert store.token_claims(tokens["access_token"]) is None


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/agents/admin/grants"),
        ("delete", "/api/agents/admin/grants/1"),
        ("delete", "/api/agents/admin/users/alice/grants"),
    ],
)
def test_admin_grant_endpoints_refuse_a_caller_outside_infra(
    platform, method, path
):
    _connect(platform)

    response = platform.request(
        method, path, headers={"Authorization": "Bearer human"}
    )

    assert response.status_code == 403


def test_admin_grant_endpoints_refuse_an_assistant_token(platform):
    tokens = _connect(platform)

    status = _as_a_tool(
        platform, tokens["access_token"], "GET", "/api/agents/admin/grants"
    )

    assert status == 403


def test_infra_lists_the_grants_of_every_user(platform):
    _connect(platform)

    grants = _live_grants(platform)

    assert [(g["username"], g["subject"]) for g in grants] == [
        ("alice", "user-1")
    ]


def test_infra_revokes_another_users_grant(platform):
    tokens = _connect(platform)
    grant_id = _live_grants(platform)[0]["id"]

    response = platform.delete(
        f"/api/agents/admin/grants/{grant_id}",
        headers={"Authorization": "Bearer infra"},
    )

    assert response.status_code == 204
    assert _call(platform, tokens["access_token"], "whoami").status_code == 401


def test_infra_revokes_every_grant_of_a_user(platform):
    _connect(platform)
    _connect(platform)

    response = platform.delete(
        "/api/agents/admin/users/alice/grants",
        headers={"Authorization": "Bearer infra"},
    )

    assert len(response.json()["revoked"]) == 2
    assert _live_grants(platform) == []


@pytest.mark.parametrize(
    "path,action",
    [
        ("/api/agents/admin/grants/7", "agent.admin.revoke"),
        ("/api/agents/admin/users/alice/grants", "agent.admin.revoke_user"),
    ],
)
def test_admin_revocations_are_journaled(path, action):
    found, _ = audit.match_action("DELETE", path)

    assert found == action


def test_an_assistant_cannot_approve_a_connection(platform):
    tokens = _connect(platform)
    client_id = _register(platform).json()["client_id"]
    _, challenge = _pkce()
    authorize = platform.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    request_id = parse_qs(urlparse(authorize.headers["location"]).query)[
        "request"
    ][0]

    status = _as_a_tool(
        platform,
        tokens["access_token"],
        "POST",
        f"/api/agents/requests/{request_id}/approve",
        json={"scopes": store.SCOPES},
    )

    assert status == 403


def test_read_only_assistant_cannot_create_an_app(platform):
    tokens = _connect(platform, scopes=[store.READ_SCOPE])

    result = _result(
        _call(
            platform,
            tokens["access_token"],
            "create_app",
            {"project": "demo", "name": "site"},
        )
    )

    assert "read-only" in result["error"]


def test_standard_project_asks_the_user_before_promoting(platform):
    tokens = _connect(platform)

    result = _result(
        _call(
            platform,
            tokens["access_token"],
            "release_action",
            {"app_id": 1, "action": "promote"},
        )
    )

    assert (result["status"], platform.promoted) == (
        "needs_user_confirmation",
        [],
    )


def test_critical_project_waits_for_a_human_approval_in_the_cmp(platform):
    tokens = _connect(platform)
    policy.set_project_stake("demo", "alice", level="critical")
    arguments = {"app_id": 1, "action": "promote"}
    asked = _result(
        _call(platform, tokens["access_token"], "release_action", arguments)
    )
    confirmation = asked["approval_link"].rsplit("/", 1)[1]
    platform.post(
        f"/api/agents/confirmations/{confirmation}/approve",
        headers={"Authorization": "Bearer human"},
    )

    done = _result(
        _call(
            platform,
            tokens["access_token"],
            "release_action",
            {**arguments, "confirmation_id": confirmation},
        )
    )
    replayed = _result(
        _call(
            platform,
            tokens["access_token"],
            "release_action",
            {**arguments, "confirmation_id": confirmation},
        )
    )

    assert (done["ok"], replayed["ok"], platform.promoted) == (
        True,
        False,
        ["alice"],
    )


def test_assistant_cannot_approve_its_own_sensitive_action(platform):
    tokens = _connect(platform)
    policy.set_project_stake("demo", "alice", level="critical")
    asked = _result(
        _call(
            platform,
            tokens["access_token"],
            "release_action",
            {"app_id": 1, "action": "promote"},
        )
    )
    confirmation = asked["approval_link"].rsplit("/", 1)[1]

    status = _as_a_tool(
        platform,
        tokens["access_token"],
        "POST",
        f"/api/agents/confirmations/{confirmation}/approve",
    )

    assert status == 403


# ---------------------------------------------------------------------------
# Stake policy
# ---------------------------------------------------------------------------


def _stake(level, autonomous_until=None):
    return policy.ProjectStake("p", level, autonomous_until)


@pytest.mark.parametrize(
    "action,level,expected",
    [
        ("release.cut", "demo", "allow"),
        ("release.cut", "standard", "ask"),
        ("release.cut", "critical", "confirm"),
        ("code.push_sensitive", "sandbox", "deny"),
        ("code.push", "critical", "pr"),
        ("app.delete", "standard", "deny"),
    ],
)
def test_policy_scales_with_the_project_stake(action, level, expected):
    decision = policy.decide(action, _stake(level))

    assert decision.outcome == expected


def test_firing_alert_lets_the_assistant_abort_on_a_critical_project():
    decision = policy.decide("release.abort", _stake("critical"), True)

    assert decision.outcome == "allow"


def test_autonomous_window_treats_a_standard_project_as_demo():
    until = datetime.now(timezone.utc) + timedelta(hours=1)

    decision = policy.decide("release.cut", _stake("standard", until))

    assert decision.outcome == "allow"


def test_expired_autonomous_window_restores_the_standard_rules():
    until = datetime.now(timezone.utc) - timedelta(minutes=1)

    decision = policy.decide("release.cut", _stake("standard", until))

    assert decision.outcome == "ask"


# ---------------------------------------------------------------------------
# Repository writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "paths,expected",
    [
        (["index.html", "src/app.js"], "code"),
        (["index.html", ".github/workflows/release.yml"], "sensitive"),
        (["backend/Dockerfile"], "sensitive"),
        (["deploy/values.yaml"], "sensitive"),
        ([".env.example"], "code"),
    ],
)
def test_paths_that_run_code_or_expose_the_app_are_sensitive(paths, expected):
    assert repo.classify(paths) == expected


@pytest.mark.parametrize("path", [".env", "certs/server.key", "a/.env.prod"])
def test_secret_files_are_refused(path):
    with pytest.raises(repo.RepoError):
        repo.classify([path])


@pytest.mark.parametrize("path", ["../outside", "/etc/../../x", "a\\b"])
def test_paths_cannot_escape_the_repository(path):
    with pytest.raises(repo.RepoError):
        repo.normalize_path(path)


def test_a_committed_token_is_detected():
    files = {"app.js": "const t = 'ghp_" + "a" * 36 + "';"}

    assert repo.find_secrets(files) == ["app.js"]


@pytest.fixture
def cut(monkeypatch):
    """cut_release with the platform's choices and GitHub faked."""
    offered = {
        "commitSha": "abc123",
        "commitMessage": "fix: x",
        "staged": True,
        "choices": [
            {"kind": "patch", "tag": "v1.0.1-rc.1"},
            {"kind": "minor", "tag": "v1.1.0-rc.1"},
        ],
    }

    async def api(method, path, **kwargs):
        if path.endswith("/release/choices"):
            return offered
        return {"id": 5, "name": "shop", "project_id": "demo"}

    caller = SimpleNamespace(
        claims={"preferred_username": "alice"},
        require_write=lambda: None,
        require_role=mock.AsyncMock(),
        require_cluster_up=mock.AsyncMock(),
        api=api,
    )
    create_tag = mock.AsyncMock(return_value="abc123")
    monkeypatch.setattr(mcp_server, "Caller", lambda: caller)
    monkeypatch.setattr(
        mcp_server, "_repo_token", mock.AsyncMock(return_value=("tok", "o/a"))
    )
    monkeypatch.setattr(
        mcp_server, "_gate", mock.AsyncMock(return_value=("go", None))
    )
    monkeypatch.setattr(mcp_server.audit, "record_agent_event", mock.Mock())
    monkeypatch.setattr(repo, "create_tag", create_tag)
    return create_tag


def test_cut_release_tags_the_commit_of_the_chosen_kind(cut):
    result = asyncio.run(mcp_server.cut_release(5, bump="minor"))

    assert result["version"] == "v1.1.0-rc.1"
    cut.assert_awaited_once_with("tok", "o/a", "v1.1.0-rc.1", sha="abc123")


def test_cut_release_approval_binds_the_tag_and_the_commit(cut):
    asyncio.run(mcp_server.cut_release(5, bump="minor"))

    assert mcp_server._gate.await_args.kwargs["arguments"] == {
        "app_id": 5,
        "bump": "minor",
        "tag": "v1.1.0-rc.1",
        "sha": "abc123",
    }


def test_cut_release_points_a_staged_app_to_the_promotion(cut):
    result = asyncio.run(mcp_server.cut_release(5, bump="minor"))

    assert "promotion" in result["next"]
    assert "get_release" not in result["next"]


def test_cut_release_names_the_kind_the_app_does_not_offer(cut):
    result = asyncio.run(mcp_server.cut_release(5, bump="candidate"))

    assert result["ok"] is False
    assert result["error"].startswith("No candidate to cut")
    cut.assert_not_awaited()


def test_an_assistant_is_slowed_down_past_its_write_budget():
    for _ in range(ratelimit.WRITES_PER_MINUTE):
        ratelimit.check(424242, "write")

    with pytest.raises(ratelimit.RateLimited):
        ratelimit.check(424242, "write")


def test_list_releases_filters_in_the_api(monkeypatch):
    api = mock.AsyncMock(
        return_value=[
            {
                "revision": 3,
                "image": "ghcr.io/o/a/backend:1.2.0",
                "outcome": "deployed",
                "started_at": "2026-11-05T10:00:00Z",
                "environment": "prod",
                "component": "backend",
            }
        ]
    )
    monkeypatch.setattr(mcp_server, "Caller", lambda: SimpleNamespace(api=api))

    result = asyncio.run(
        mcp_server.list_releases(
            5, environment="prod", component="backend", version="1.2", limit=3
        )
    )

    api.assert_awaited_once_with(
        "GET",
        "/api/deployments/5/releases",
        params={
            "limit": 3,
            "environment": "prod",
            "component": "backend",
            "version": "1.2",
        },
    )
    assert result["releases"][0]["environment"] == "prod"
    assert result["releases"][0]["component"] == "backend"


@pytest.fixture
def project_api(monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"status": "provisioning", "target_cloud": "aws"}

    caller = SimpleNamespace(
        username="alice", require_write=lambda: None, api=api
    )
    monkeypatch.setattr(mcp_server, "Caller", lambda: caller)
    monkeypatch.setattr(
        mcp_server, "_gate", mock.AsyncMock(return_value=("go", None))
    )
    monkeypatch.setattr(mcp_server.policy, "set_project_stake", mock.Mock())
    return calls


def test_create_project_sends_the_chosen_cloud(project_api):
    result = asyncio.run(
        mcp_server.create_project("probe", stake="demo", cloud="aws")
    )

    assert project_api[0][2]["json"] == {
        "project_name": "probe",
        "target_cloud": "aws",
    }
    assert result["cloud"] == "aws"


def test_create_project_approval_names_the_cloud(project_api):
    asyncio.run(mcp_server.create_project("probe", cloud="gcp"))

    assert mcp_server._gate.await_args.kwargs["arguments"]["cloud"] == "gcp"


def test_create_project_stays_on_prem_by_default(project_api):
    asyncio.run(mcp_server.create_project("probe"))

    assert project_api[0][2]["json"]["target_cloud"] == "onprem"


def test_whoami_lists_each_cloud_state_and_project_cloud(monkeypatch):
    async def api(method, path, **kwargs):
        return [{"name": "shop", "role": "owner", "target_cloud": "aws"}]

    caller = SimpleNamespace(
        api=api,
        username="alice",
        claims={"name": "Alice", "cnp_agent": {"client": "Claude"}},
        scopes=[],
    )
    monkeypatch.setattr(mcp_server, "Caller", lambda: caller)
    monkeypatch.setattr(
        mcp_server.policy,
        "project_stake",
        lambda name: SimpleNamespace(effective_level="demo"),
    )
    monkeypatch.setattr(
        mcp_server.skills, "list_skills", mock.AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        mcp_server,
        "cluster_states",
        mock.AsyncMock(
            return_value={TargetCloud.AWS: "up", TargetCloud.GCP: "down"}
        ),
    )

    result = asyncio.run(mcp_server.whoami())

    assert result["clouds"] == {"onprem": "up", "aws": "up", "gcp": "down"}
    assert result["projects"][0]["cloud"] == "aws"


# ---------------------------------------------------------------------------
# Step-up
# ---------------------------------------------------------------------------


def _step_up_on():
    return mock.patch.object(settings, "STEP_UP_ENABLED", True)


def _pending_request(client) -> str:
    client_id = _register(client).json()["client_id"]
    _, challenge = _pkce()
    authorize = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    return parse_qs(urlparse(authorize.headers["location"]).query)["request"][
        0
    ]


def test_connecting_an_assistant_needs_a_fresh_passkey_login(platform):
    # Arrange
    request_id = _pending_request(platform)

    # Act
    with _step_up_on():
        response = platform.post(
            f"/api/agents/requests/{request_id}/approve",
            json={"scopes": store.SCOPES},
            headers={"Authorization": "Bearer human"},
        )

    # Assert
    assert (response.status_code, response.json()["detail"]["code"]) == (
        401,
        "step_up_required",
    )


def test_approving_an_assistant_request_in_the_cmp_needs_a_fresh_passkey_login(
    platform,
):
    # Arrange
    tokens = _connect(platform)
    policy.set_project_stake("demo", "alice", level="critical")
    asked = _result(
        _call(
            platform,
            tokens["access_token"],
            "release_action",
            {"app_id": 1, "action": "promote"},
        )
    )
    confirmation = asked["approval_link"].rsplit("/", 1)[1]

    # Act
    with _step_up_on():
        response = platform.post(
            f"/api/agents/confirmations/{confirmation}/approve",
            headers={"Authorization": "Bearer human"},
        )

    # Assert
    assert (
        response.status_code,
        policy.load_confirmation(confirmation).status,
    ) == (
        401,
        "pending",
    )


@pytest.mark.parametrize(
    "tool, arguments",
    [
        (
            "release_action",
            {
                "app_id": 1,
                "action": "promote",
                "full": True,
                "reason": "hotfix",
                "user_confirmed": True,
            },
        ),
        ("delete_app", {"app_id": 1, "user_confirmed": True}),
    ],
)
def test_an_assistant_hands_passkey_actions_to_the_user_in_the_cmp(
    platform, tool, arguments
):
    # Arrange
    tokens = _connect(platform)
    policy.set_project_stake("demo", "alice", level="sandbox")

    # Act
    with _step_up_on():
        result = _result(
            _call(platform, tokens["access_token"], tool, arguments)
        )

    # Assert
    assert (result["status"], result["cmp_link"], platform.promoted) == (
        "not_allowed_for_assistants",
        f"{mcp_server.PUBLIC_URL}/projects/demo/apps/1",
        [],
    )
