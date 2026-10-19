import base64
import contextlib
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
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
from app.core.database import Base, get_db
from app.routers import agents as agents_router
from app.services.agents import internal_api, policy, repo, skills, store
from app.services.keycloak_service import (
    decode_platform_token,
    get_current_user,
    security,
)

HUMAN = {"sub": "user-1", "preferred_username": "alice", "name": "Alice"}
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
        return HUMAN

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

    response = platform.post(
        f"/api/agents/requests/{request_id}/approve",
        json={"scopes": store.SCOPES},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 403


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

    response = platform.post(
        f"/api/agents/confirmations/{confirmation}/approve",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 403


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


@pytest.mark.parametrize(
    "current,bump,expected",
    [
        (None, "patch", "v0.1.0"),
        ("v1.2.3", "patch", "v1.2.4"),
        ("v1.2.3", "minor", "v1.3.0"),
        ("v1.2.3", "major", "v2.0.0"),
    ],
)
def test_next_version_follows_semver(current, bump, expected):
    assert repo.next_version(current, bump) == expected
