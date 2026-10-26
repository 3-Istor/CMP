"""Existing installation linking must verify the portal and GitHub identities."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.models.user_github import UserGitHubInstallation
from app.routers import account
from app.services import github_account as service
from app.services import keycloak_service


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def claims():
    now = int(datetime.now(timezone.utc).timestamp())
    return {
        "sub": "user-id",
        "preferred_username": "joe",
        "aud": settings.IMPORT_USER_AUDIENCE,
        "azp": settings.KEYCLOAK_CLIENT_ID,
        "iat": now,
        "exp": now + 300,
        "iss": keycloak_service._ISSUER,
    }


def installation(**changes):
    return {
        "id": 123,
        "app_id": int(service.github_service.GITHUB_APP_ID),
        "suspended_at": None,
        "account": {"login": "example-org", "type": "Organization", "id": 77},
        **changes,
    }


def test_missing_link_stops_before_github_or_database_mutation(db):
    with patch.object(
        service, "identity_for_user", return_value=("user-id", None)
    ), patch.object(service, "verify_installation", AsyncMock()) as verify:
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.connect(db, claims()))
    assert error.value.status_code == 409
    verify.assert_not_awaited()
    assert db.query(UserGitHubInstallation).count() == 0


@pytest.mark.parametrize("legacy", [False, True])
def test_existing_installation_links_for_an_ordinary_member_without_reinstallation(
    db, legacy
):
    if legacy:
        db.add(UserGitHubInstallation(user_sub="joe", installation_id="999"))
        db.commit()
    api = AsyncMock(
        side_effect=[
            installation(),
            {"id": 42, "login": "joe"},
            {"state": "active", "role": "member"},
        ]
    )
    with patch.object(settings, "GITHUB_INSTALLATION_ID", "123"), patch.object(
        service,
        "identity_for_user",
        return_value=("user-id", {"login": "joe", "id": "42"}),
    ), patch.object(service, "api", api), patch.object(
        service.github_service, "generate_jwt", return_value="app-jwt"
    ), patch.object(
        service.github_service,
        "get_installation_token",
        AsyncMock(return_value="read-only"),
    ) as token:
        result = asyncio.run(service.connect(db, claims()))
    row = db.query(UserGitHubInstallation).one()
    assert (row.user_sub, row.installation_id) == ("user-id", "123")
    assert result["github_login"] == "joe"
    token.assert_awaited_once_with("123", permissions={"members": "read"})
    assert [call.args[0] for call in api.call_args_list] == [
        "/app/installations/123",
        "/users/joe",
        "/orgs/example-org/memberships/joe",
    ]


@pytest.mark.parametrize(
    "failure",
    [
        "wrong-app",
        "suspended",
        "changed-identity",
        "pending",
        "not-member",
        "other-personal-account",
    ],
)
def test_invalid_installations_or_memberships_never_save_a_link(db, failure):
    meta = installation()
    user = {"id": 42, "login": "joe"}
    membership = {"state": "active"}
    if failure == "wrong-app":
        meta["app_id"] = -1
    if failure == "suspended":
        meta["suspended_at"] = "2026-01-01"
    if failure == "changed-identity":
        user["id"] = 99
    if failure == "pending":
        membership["state"] = "pending"
    if failure == "not-member":
        membership = HTTPException(403, "not a member")
    if failure == "other-personal-account":
        meta["account"] = {"id": 99, "login": "other", "type": "User"}
    with patch.object(
        service,
        "identity_for_user",
        return_value=("user-id", {"login": "joe", "id": "42"}),
    ), patch.object(
        service, "api", AsyncMock(side_effect=[meta, user, membership])
    ), patch.object(
        service.github_service, "generate_jwt", return_value="app-jwt"
    ), patch.object(
        service.github_service,
        "get_installation_token",
        AsyncMock(return_value="test"),
    ):
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.connect(db, claims(), "123"))
    assert error.value.status_code == 403
    assert db.query(UserGitHubInstallation).count() == 0


def test_duplicate_legacy_record_is_consolidated_after_verification(db):
    db.add_all(
        [
            UserGitHubInstallation(user_sub="joe", installation_id="old"),
            UserGitHubInstallation(user_sub="user-id", installation_id="123"),
        ]
    )
    db.commit()
    with patch.object(
        service,
        "identity_for_user",
        return_value=("user-id", {"login": "joe", "id": "42"}),
    ), patch.object(
        service,
        "verify_installation",
        AsyncMock(return_value={"login": "joe", "account": "example-org"}),
    ):
        asyncio.run(service.connect(db, claims(), "123"))
        asyncio.run(service.connect(db, claims(), "123"))
    assert db.query(UserGitHubInstallation).count() == 1


def test_status_does_not_claim_linked_for_installation_id_without_github_identity(
    db,
):
    db.add(UserGitHubInstallation(user_sub="joe", installation_id="123"))
    db.commit()
    with patch.object(
        service, "identity_for_user", return_value=("user-id", None)
    ):
        status = asyncio.run(service.connection_status(db, claims()))
    assert status["identity_linked"] is False
    assert status["github_installation_id"] is None
    assert db.query(UserGitHubInstallation).one().user_sub == "joe"


def test_user_profile_can_read_the_canonical_link_for_existing_catalog_callers(
    db,
):
    db.add(UserGitHubInstallation(user_sub="user-id", installation_id="123"))
    db.commit()
    with patch.object(
        account, "_get_admin_token", side_effect=RuntimeError("unavailable")
    ):
        profile = account.get_user_profile(claims(), db)
    assert profile.github_installation_id == "123"


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
def test_link_routes_reject_invalid_browser_tokens_before_identity_checks(
    db, changed
):
    app = FastAPI()
    app.include_router(account.router)
    app.dependency_overrides[get_db] = lambda: db
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signed = jwt.encode(
        {**claims(), **changed},
        key,
        algorithm="RS256",
        headers={"kid": "test"},
    )
    with patch.object(
        keycloak_service._jwks_client,
        "get_signing_key_from_jwt",
        return_value=SimpleNamespace(key=key.public_key()),
    ), patch.object(service, "identity_for_user") as identity:
        with TestClient(app) as client:
            response = client.post(
                "/account/github-connect",
                headers={"Authorization": "Bearer " + signed},
            )
    assert response.status_code == 401
    identity.assert_not_called()
    assert db.query(UserGitHubInstallation).count() == 0


def test_legacy_manual_route_cannot_bypass_federated_identity(db):
    app = FastAPI()
    app.include_router(account.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[service.portal_user] = claims
    with patch.object(
        service, "identity_for_user", return_value=("user-id", None)
    ):
        with TestClient(app) as client:
            response = client.post(
                "/account/github-installation", json={"installation_id": "123"}
            )
    assert response.status_code == 409
    assert db.query(UserGitHubInstallation).count() == 0


def test_import_jwks_uses_the_user_agent_required_by_the_existing_keycloak_proxy():
    from app.services import repository_imports

    repository_imports.jwks.cache_clear()
    with patch.object(repository_imports.jwt, "PyJWKClient") as client:
        repository_imports.jwks("https://keycloak.example.org/certs")
    assert client.call_args.kwargs["headers"] == {"User-Agent": "cnp-backend"}
    repository_imports.jwks.cache_clear()


@pytest.mark.parametrize("code", [403, 503])
def test_status_does_not_trust_an_old_saved_installation_when_access_is_revoked(
    db, code
):
    db.add(UserGitHubInstallation(user_sub="user-id", installation_id="123"))
    db.commit()
    with patch.object(
        service,
        "identity_for_user",
        return_value=("user-id", {"login": "joe", "id": "42"}),
    ), patch.object(
        service,
        "verify_installation",
        AsyncMock(side_effect=HTTPException(code, "unavailable")),
    ):
        if code == 503:
            with pytest.raises(HTTPException):
                asyncio.run(service.connection_status(db, claims()))
        else:
            assert (
                asyncio.run(service.connection_status(db, claims()))[
                    "github_installation_id"
                ]
                is None
            )
    assert db.query(UserGitHubInstallation).one().installation_id == "123"


@pytest.mark.parametrize("permissions", [None, {"members": "read"}])
def test_installation_token_can_be_restricted_without_changing_existing_callers(
    permissions,
):
    import httpx

    response = httpx.Response(
        200,
        json={"token": "test-only"},
        request=httpx.Request("POST", "https://api.github.com/test"),
    )
    client = AsyncMock()
    client.post.return_value = response
    with patch.object(
        service.github_service, "generate_jwt", return_value="test-jwt"
    ), patch.object(service.github_service.httpx, "AsyncClient") as factory:
        factory.return_value.__aenter__.return_value = client
        assert (
            asyncio.run(
                service.github_service.get_installation_token(
                    "123", permissions=permissions
                )
            )
            == "test-only"
        )
    if permissions is None:
        assert "json" not in client.post.call_args.kwargs
    else:
        assert client.post.call_args.kwargs["json"] == {
            "permissions": {"members": "read"}
        }


@pytest.mark.parametrize(
    "link_url",
    [
        "",
        "https://sso.example.org/realms/cnp/account/account-security/linked-accounts",
    ],
)
def test_account_link_is_only_offered_after_operator_configuration(
    db, link_url
):
    with patch.object(
        settings, "GITHUB_ACCOUNT_LINK_URL", link_url
    ), patch.object(
        service, "identity_for_user", return_value=("user-id", None)
    ):
        status = asyncio.run(service.connection_status(db, claims()))
    assert status["account_url"] == (link_url or None)
    assert status["github_installation_id"] is None
    assert db.query(UserGitHubInstallation).count() == 0
