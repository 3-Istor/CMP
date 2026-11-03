"""Direct GitHub OAuth proofs: real SQL/crypto, external services simulated."""

import asyncio
import hashlib
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.models.user_github import (
    GitHubOAuthCredential,
    GitHubOAuthState,
    UserGitHubInstallation,
)
from app.routers import account
from app.services import github_account as service
from app.services import github_service, keycloak_service

BINDING = "browser-session-" + "a" * 40
ACCESS = "fake-user-access-token-only-for-tests"
REFRESH = "fake-user-refresh-token-only-for-tests"
APP_ID = int(github_service.GITHUB_APP_ID)


def claims(**changes):
    return {
        "sub": "cnp-user",
        "preferred_username": "joe",
        "sid": "cnp-session",
        "aud": settings.IMPORT_USER_AUDIENCE,
        "azp": settings.KEYCLOAK_CLIENT_ID,
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        **changes,
    }


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_ID", "Iv1.test-client")
    monkeypatch.setattr(
        settings, "GITHUB_OAUTH_CLIENT_SECRET", "test-client-secret"
    )
    monkeypatch.setattr(
        settings,
        "GITHUB_OAUTH_REDIRECT_URI",
        "http://localhost:3000/api/auth/github/callback",
    )
    monkeypatch.setattr(
        settings, "GITHUB_OAUTH_TOKEN_KEY", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(service, "cnp_user_id", lambda token: token["sub"])
    monkeypatch.setattr(github_service, "generate_jwt", lambda: "fake-app-jwt")


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def tokens(**changes):
    return {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "token_type": "bearer",
        "expires_in": 28800,
        "refresh_token_expires_in": 15897600,
        **changes,
    }


def store(db, **changes):
    row = GitHubOAuthCredential(
        user_sub="cnp-user",
        github_user_id="42",
        github_login="joe-github",
        credentials_ciphertext=service.encrypt(
            {"access_token": ACCESS, "refresh_token": REFRESH}
        ),
        access_expires_at=service.now() + timedelta(hours=8),
        refresh_expires_at=service.now() + timedelta(days=180),
        version=1,
        **changes,
    )
    db.add(row)
    db.commit()
    return row


def installation(**changes):
    return {
        "id": 123,
        "app_id": APP_ID,
        "suspended_at": None,
        "account": {"id": 77, "login": "my-org", "type": "Organization"},
        **changes,
    }


async def api(path, _token, **_kwargs):
    if path == "/user":
        return {"id": 42, "login": "joe-github"}
    if path == "/app":
        return {"id": APP_ID, "slug": "cnp-portal"}
    if path == "/user/installations":
        return {"installations": [installation()]}
    if path == "/app/installations/123":
        return installation()
    if path.endswith("/repositories"):
        return {
            "repositories": [
                {
                    "id": 10,
                    "full_name": "my-org/private",
                    "html_url": "https://github.com/my-org/private",
                    "permissions": {"pull": True, "push": True},
                }
            ]
        }
    raise AssertionError(path)


def begin(db, mode="authorize"):
    result = asyncio.run(service.begin(db, claims(), BINDING, mode))
    return (
        result,
        parse_qs(urlsplit(result["authorization_url"]).query)["state"][0],
    )


def test_begin_uses_session_bound_hashed_state_pkce_and_expiring_tokens(db):
    result, state = begin(db)
    row = db.get(GitHubOAuthState, hashlib.sha256(state.encode()).hexdigest())
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert row.user_sub == "cnp-user" and row.consumed_at is None
    assert row.session_hash != BINDING and state not in row.state_hash
    assert query["scope"] == ["offline_access"] and query[
        "code_challenge_method"
    ] == ["S256"]
    verifier = service.decrypt(row.verifier_ciphertext)["verifier"]
    assert (
        verifier not in row.verifier_ciphertext
        and verifier not in result["authorization_url"]
    )
    assert query["code_challenge"] == [
        service.base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    ]


@pytest.mark.parametrize(
    "changed,binding",
    [
        ({"sub": "another-user"}, BINDING),
        ({"sid": "another-session"}, BINDING),
        ({}, "b" * 64),
    ],
)
def test_foreign_user_or_browser_cannot_consume_state(db, changed, binding):
    _, state = begin(db)
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        with pytest.raises(HTTPException):
            asyncio.run(
                service.callback(db, claims(**changed), state, "code", binding)
            )
    exchange.assert_not_awaited()
    assert (
        db.get(
            GitHubOAuthState, hashlib.sha256(state.encode()).hexdigest()
        ).consumed_at
        is None
    )


def test_expired_and_replayed_callbacks_never_exchange_a_code_twice(db):
    _, expired = begin(db)
    row = db.get(
        GitHubOAuthState, hashlib.sha256(expired.encode()).hexdigest()
    )
    row.expires_at = service.now() - timedelta(seconds=1)
    db.commit()
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        with pytest.raises(HTTPException):
            asyncio.run(
                service.callback(db, claims(), expired, "code", BINDING)
            )
    exchange.assert_not_awaited()
    _, state = begin(db)
    with patch.object(
        service, "exchange", AsyncMock(return_value=tokens())
    ) as exchange, patch.object(service, "api", AsyncMock(side_effect=api)):
        result = asyncio.run(
            service.callback(db, claims(), state, "code", BINDING)
        )
        with pytest.raises(HTTPException):
            asyncio.run(service.callback(db, claims(), state, "code", BINDING))
    assert result == {"github_login": "joe-github", "identity_linked": True}
    exchange.assert_awaited_once()
    row = db.get(GitHubOAuthCredential, "cnp-user")
    assert (
        ACCESS not in row.credentials_ciphertext
        and REFRESH not in row.credentials_ciphertext
    )
    assert ACCESS not in str(result) and REFRESH not in str(result)
    assert db.query(UserGitHubInstallation).count() == 0


def test_bad_exchange_consumes_state_before_failure(db):
    _, state = begin(db)
    with patch.object(
        service,
        "exchange",
        AsyncMock(side_effect=HTTPException(403, "invalid code")),
    ):
        with pytest.raises(HTTPException):
            asyncio.run(
                service.callback(db, claims(), state, "bad-code", BINDING)
            )
    db.expire_all()
    assert (
        db.get(
            GitHubOAuthState, hashlib.sha256(state.encode()).hexdigest()
        ).consumed_at
        is not None
    )
    assert db.query(GitHubOAuthCredential).count() == 0


@pytest.mark.parametrize("code", [None, "untrusted-installation-oauth-code"])
def test_installation_callback_chains_to_new_pkce_without_trusting_installation_hints(
    db, code
):
    with patch.object(
        service,
        "app_info",
        AsyncMock(return_value={"id": APP_ID, "slug": "cnp-portal"}),
    ):
        start, state = begin(db, "install")
    assert (
        urlsplit(start["authorization_url"]).path
        == "/apps/cnp-portal/installations/new"
    )
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        result = asyncio.run(
            service.callback(db, claims(), state, code, BINDING)
        )
    exchange.assert_not_awaited()
    assert (
        urlsplit(result["authorization_url"]).path == "/login/oauth/authorize"
    )
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert query["state"] != [state] and query["code_challenge_method"] == [
        "S256"
    ]
    assert db.query(GitHubOAuthCredential).count() == 0
    assert db.query(UserGitHubInstallation).count() == 0


def test_new_oauth_identity_clears_an_old_selected_installation(db):
    db.add(UserGitHubInstallation(user_sub="cnp-user", installation_id="999"))
    db.commit()
    _, state = begin(db)
    with patch.object(
        service, "exchange", AsyncMock(return_value=tokens())
    ), patch.object(service, "api", AsyncMock(side_effect=api)):
        asyncio.run(service.callback(db, claims(), state, "code", BINDING))
    assert db.query(UserGitHubInstallation).count() == 0


def test_missing_or_wrong_encryption_key_never_returns_credentials(
    db, monkeypatch
):
    store(db)
    monkeypatch.setattr(
        settings, "GITHUB_OAUTH_TOKEN_KEY", Fernet.generate_key().decode()
    )
    with pytest.raises(HTTPException):
        asyncio.run(service.fresh_credentials(db, "cnp-user"))
    monkeypatch.setattr(settings, "GITHUB_OAUTH_TOKEN_KEY", "")
    with pytest.raises(HTTPException):
        asyncio.run(service.fresh_credentials(db, "cnp-user"))


def test_fresh_proof_verifies_user_and_the_actual_selected_installation(db):
    store(db)
    with patch.object(service, "api", AsyncMock(side_effect=api)) as github:
        result = asyncio.run(
            service.repository_identity(db, "cnp-user", "123")
        )
    assert result == {"id": "42", "login": "joe-github", "token": ACCESS}
    assert any(
        call.args[0] == "/user/installations" and call.args[1] == ACCESS
        for call in github.call_args_list
    )


@pytest.mark.parametrize(
    "failure", ["user", "missing", "wrong-app", "suspended", "other-account"]
)
def test_revoked_or_mismatched_installations_never_create_a_saved_link(
    db, failure
):
    store(db)

    async def invalid(path, token, **kwargs):
        if path == "/user" and failure == "user":
            return {"id": 999, "login": "another"}
        if path == "/user/installations" and failure == "missing":
            return {"installations": []}
        if path == "/app/installations/123":
            return installation(
                **(
                    {"app_id": 999}
                    if failure == "wrong-app"
                    else (
                        {"suspended_at": "now"}
                        if failure == "suspended"
                        else (
                            {"account": {"id": 999}}
                            if failure == "other-account"
                            else {}
                        )
                    )
                )
            )
        return await api(path, token, **kwargs)

    with patch.object(service, "api", AsyncMock(side_effect=invalid)):
        with pytest.raises(HTTPException):
            asyncio.run(service.connect(db, claims(), "123"))
    assert db.query(UserGitHubInstallation).count() == 0


def test_installations_and_repositories_are_paginated_before_permission_decision(
    db,
):
    store(db)
    seen = []

    async def paginated(path, token, **kwargs):
        if path == "/user/installations":
            page = kwargs["params"]["page"]
            seen.append(("installations", page))
            return {
                "installations": (
                    [installation(id=1000 + i) for i in range(100)]
                    if page == 1
                    else [installation()]
                )
            }
        if path.endswith("/repositories"):
            page = kwargs["params"]["page"]
            seen.append(("repositories", page))
            return {
                "repositories": (
                    [{"full_name": f"my-org/other-{i}"} for i in range(100)]
                    if page == 1
                    else [
                        {
                            "id": 10,
                            "full_name": "my-org/private",
                            "permissions": {"pull": True, "push": True},
                        }
                    ]
                )
            }
        return await api(path, token, **kwargs)

    with patch.object(service, "api", AsyncMock(side_effect=paginated)):
        result = asyncio.run(
            service.repository_access(
                db, "cnp-user", "123", "my-org/private", write=True
            )
        )
    assert result["repository"]["id"] == 10
    assert seen == [
        ("installations", 1),
        ("installations", 2),
        ("repositories", 1),
        ("repositories", 2),
    ]


@pytest.mark.parametrize(
    "name,permissions,write",
    [
        ("other-org/private", {"pull": True, "push": True}, False),
        ("my-org/private", {}, False),
        ("my-org/private", {"pull": True, "push": False}, True),
    ],
)
def test_repo_owner_and_current_permissions_cannot_be_bypassed(
    db, name, permissions, write
):
    store(db)

    async def limited(path, token, **kwargs):
        if path.endswith("/repositories"):
            return {
                "repositories": [
                    {"id": 10, "full_name": name, "permissions": permissions}
                ]
            }
        return await api(path, token, **kwargs)

    with patch.object(service, "api", AsyncMock(side_effect=limited)):
        with pytest.raises(HTTPException):
            asyncio.run(
                service.repository_access(
                    db, "cnp-user", "123", "my-org/private", write=write
                )
            )


def test_only_an_explicit_verified_installation_is_saved_no_platform_fallback(
    db, monkeypatch
):
    store(db)
    monkeypatch.setattr(
        settings,
        "GITHUB_INSTALLATION_ID",
        "platform-installation-must-not-be-selected",
    )
    with patch.object(service, "api", AsyncMock(side_effect=api)):
        with pytest.raises(HTTPException):
            asyncio.run(service.connect(db, claims(), ""))
        result = asyncio.run(service.connect(db, claims(), "123"))
    assert result["account"] == "my-org"
    assert db.query(UserGitHubInstallation).one().installation_id == "123"


def test_refresh_rotates_encrypted_tokens_without_flushing_callers_pending_grant(
    db,
):
    row = store(db)
    row.access_expires_at = service.now() - timedelta(seconds=1)
    link = UserGitHubInstallation(user_sub="cnp-user", installation_id="123")
    db.add(link)
    db.commit()
    link.installation_id = "uncommitted-caller-change"
    refreshed = tokens(access_token="new-access", refresh_token="new-refresh")
    with patch.object(
        db, "commit", side_effect=AssertionError("must not commit caller")
    ), patch.object(
        db, "flush", side_effect=AssertionError("must not flush caller")
    ), patch.object(
        service, "exchange", AsyncMock(return_value=refreshed)
    ) as exchange, patch.object(
        service, "api", AsyncMock(side_effect=api)
    ):
        proof = asyncio.run(service.fresh_credentials(db, "cnp-user"))
    assert proof["token"] == "new-access" and db.is_modified(link)
    assert exchange.call_args.args[0] == {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH,
    }
    with Session(db.get_bind()) as other:
        assert (
            other.scalar(select(UserGitHubInstallation.installation_id))
            == "123"
        )
        stored = other.get(GitHubOAuthCredential, "cnp-user")
        assert stored.version == 2 and stored.refreshing_until is None
        assert (
            service.decrypt(stored.credentials_ciphertext)["refresh_token"]
            == "new-refresh"
        )


def test_refresh_lease_prevents_consuming_the_same_refresh_token_twice(db):
    row = store(db)
    row.access_expires_at = service.now() - timedelta(seconds=1)
    row.refreshing_until = service.now() + timedelta(seconds=60)
    db.commit()
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.fresh_credentials(db, "cnp-user"))
    assert error.value.status_code == 503
    exchange.assert_not_awaited()


def test_expired_refresh_or_revoked_access_fails_closed(db):
    row = store(db)
    row.access_expires_at = row.refresh_expires_at = service.now() - timedelta(
        seconds=1
    )
    db.commit()
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        with pytest.raises(HTTPException):
            asyncio.run(service.fresh_credentials(db, "cnp-user"))
    exchange.assert_not_awaited()
    row.access_expires_at = service.now() + timedelta(hours=1)
    db.commit()
    with patch.object(
        service, "api", AsyncMock(side_effect=HTTPException(403, "revoked"))
    ):
        with pytest.raises(HTTPException):
            asyncio.run(service.repository_identity(db, "cnp-user", "123"))


def test_a_new_oauth_callback_wins_over_a_delayed_refresh(db):
    row = store(db)
    row.access_expires_at = service.now() - timedelta(seconds=1)
    db.commit()

    async def replace_during_refresh(_parameters):
        with Session(db.get_bind()) as other:
            other.execute(
                update(GitHubOAuthCredential)
                .where(GitHubOAuthCredential.user_sub == "cnp-user")
                .values(
                    version=2,
                    refreshing_until=None,
                    credentials_ciphertext=service.encrypt(
                        {
                            "access_token": "new-oauth",
                            "refresh_token": "new-oauth-refresh",
                        }
                    ),
                )
            )
            other.commit()
        return tokens(access_token="old-refresh-result")

    with patch.object(
        service, "exchange", AsyncMock(side_effect=replace_during_refresh)
    ), patch.object(service, "api", AsyncMock(side_effect=api)):
        with pytest.raises(HTTPException):
            asyncio.run(service.fresh_credentials(db, "cnp-user"))
    db.expire_all()
    assert (
        service.decrypt(
            db.get(GitHubOAuthCredential, "cnp-user").credentials_ciphertext
        )["access_token"]
        == "new-oauth"
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"aud": "other"},
        {"azp": "other"},
        {"exp": 1},
        {"iat": None},
        {"preferred_username": ""},
    ],
)
def test_link_routes_reject_invalid_cnp_browser_tokens_before_oauth_or_db(
    db, changed
):
    app = FastAPI()
    app.include_router(account.router)
    app.dependency_overrides[get_db] = lambda: db
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signed = jwt.encode(
        claims(**changed), key, algorithm="RS256", headers={"kid": "test"}
    )
    with patch.object(
        keycloak_service._jwks_client,
        "get_signing_key_from_jwt",
        return_value=SimpleNamespace(key=key.public_key()),
    ), patch.object(service, "begin", AsyncMock()) as begin_oauth:
        with TestClient(app) as client:
            response = client.post(
                "/account/github/oauth/start",
                json={"binding": BINDING},
                headers={"Authorization": "Bearer " + signed},
            )
    assert response.status_code == 401
    begin_oauth.assert_not_awaited()
    assert db.query(GitHubOAuthState).count() == 0


def test_legacy_manual_route_cannot_bypass_oauth_proof(db):
    app = FastAPI()
    app.include_router(account.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[service.portal_user] = lambda: claims()
    with TestClient(app) as client:
        response = client.post(
            "/account/github-installation", json={"installation_id": "123"}
        )
    assert response.status_code == 403
    assert db.query(UserGitHubInstallation).count() == 0


def test_user_profile_keeps_canonical_link_for_existing_catalogue_callers(db):
    db.add(UserGitHubInstallation(user_sub="cnp-user", installation_id="123"))
    db.commit()
    with patch.object(
        account, "_get_admin_token", side_effect=RuntimeError("unavailable")
    ):
        profile = account.get_user_profile(claims(), db)
    assert profile.github_installation_id == "123"


def test_installation_token_permissions_for_existing_callers_are_unchanged():
    response = Mock(status_code=201)
    response.json.return_value = {"token": "installation-token"}
    client = AsyncMock()
    client.post.return_value = response
    with patch.object(
        github_service, "generate_jwt", return_value="app"
    ), patch.object(github_service.httpx, "AsyncClient") as factory:
        factory.return_value.__aenter__.return_value = client
        assert (
            asyncio.run(
                github_service.get_installation_token(
                    "123", permissions={"contents": "read"}
                )
            )
            == "installation-token"
        )
    assert client.post.call_args.kwargs["json"] == {
        "permissions": {"contents": "read"}
    }


def test_configured_flag_has_no_secret_values(db):
    with patch.object(service, "api", AsyncMock(side_effect=api)):
        result = asyncio.run(service.connection_status(db, claims()))
    assert (
        result["oauth_configured"] is True
        and result["identity_linked"] is False
    )
    assert result["github_installation_id"] is None
    assert settings.GITHUB_OAUTH_CLIENT_SECRET not in str(result)
    assert settings.GITHUB_OAUTH_TOKEN_KEY not in str(result)


def test_api_errors_never_copy_upstream_tokens_to_the_client():
    response = httpx.Response(
        401,
        request=httpx.Request("GET", "https://api.github.com/user"),
        json={"message": ACCESS},
    )
    client = AsyncMock()
    client.get.return_value = response
    with patch.object(service.httpx, "AsyncClient") as factory:
        factory.return_value.__aenter__.return_value = client
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.api("/user", ACCESS))
    assert ACCESS not in error.value.detail


def test_concurrent_callback_state_is_consumed_before_second_exchange(db):
    _, state = begin(db)

    async def exchange_after_consumption(_parameters):
        with Session(db.get_bind()) as other:
            with pytest.raises(HTTPException):
                await service.callback(
                    other, claims(), state, "second-code", BINDING
                )
        return tokens()

    with patch.object(
        service, "exchange", AsyncMock(side_effect=exchange_after_consumption)
    ) as exchange, patch.object(service, "api", AsyncMock(side_effect=api)):
        asyncio.run(
            service.callback(db, claims(), state, "first-code", BINDING)
        )
    exchange.assert_awaited_once()
    assert db.query(GitHubOAuthCredential).count() == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_in": None},
        {"expires_in": 0},
        {"refresh_token": ""},
        {"refresh_token_expires_in": None},
        {"token_type": "basic"},
    ],
)
def test_unbounded_or_malformed_oauth_grants_are_rejected(changes):
    response = httpx.Response(
        200,
        request=httpx.Request("POST", service.OAUTH_URL),
        json=tokens(**changes),
    )
    client = AsyncMock()
    client.post.return_value = response
    with patch.object(service.httpx, "AsyncClient") as factory:
        factory.return_value.__aenter__.return_value = client
        with pytest.raises(HTTPException):
            asyncio.run(
                service.exchange(
                    {"code": "test-code", "code_verifier": "test-verifier"}
                )
            )


def test_revoked_ui_connection_returns_reconnect_action_without_claiming_linked(
    db,
):
    store(db)

    async def revoked(path, token, **kwargs):
        if path == "/user":
            raise HTTPException(403, "revoked")
        return await api(path, token, **kwargs)

    with patch.object(service, "api", AsyncMock(side_effect=revoked)):
        result = asyncio.run(service.connection_status(db, claims()))
    assert (
        result["identity_linked"] is False
        and result["github_installation_id"] is None
    )
    assert result["oauth_configured"] is True and result["installation_url"]
