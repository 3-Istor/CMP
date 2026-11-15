"""GitHub account linking: real SQL and crypto, GitHub simulated."""

import asyncio
import base64
import hashlib
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
from app.services import github_service
from app.services.keycloak_service import get_current_user

BINDING = "browser-binding-" + "a" * 40
ACCESS = "fake-access-token-for-tests"
REFRESH = "fake-refresh-token-for-tests"
APP_ID = int(github_service.GITHUB_APP_ID)


def claims(**changes):
    return {
        "sub": "cnp-user",
        "preferred_username": "joe",
        "sid": "cnp-session",
        **changes,
    }


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_ID", "Iv1.test")
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        settings,
        "GITHUB_OAUTH_REDIRECT_URI",
        "https://cmp.example.com/api/auth/github/callback",
    )
    monkeypatch.setattr(settings, "GITHUB_APP_SLUG", "cnp-portal")
    monkeypatch.setattr(
        settings,
        "GITHUB_TOKEN_ENCRYPTION_KEYS",
        Fernet.generate_key().decode(),
    )
    monkeypatch.setattr(service, "cnp_user_id", lambda token: token["sub"])


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
    values = {
        "user_sub": "cnp-user",
        "github_user_id": "42",
        "github_login": "joe-github",
        "credentials_ciphertext": service.encrypt(
            {"access_token": ACCESS, "refresh_token": REFRESH}
        ),
        "access_expires_at": service.now() + timedelta(hours=8),
        "refresh_expires_at": service.now() + timedelta(days=180),
        "version": 1,
        **changes,
    }
    row = GitHubOAuthCredential(**values)
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


def repo(name, repo_id, **changes):
    return {
        "id": repo_id,
        "full_name": name,
        "default_branch": "main",
        "private": True,
        "permissions": {"pull": True, "push": False, "admin": False},
        **changes,
    }


INSTALLATIONS = [
    installation(),
    installation(id=124, app_id=999),
    installation(id=125, suspended_at="2026-01-01T00:00:00Z"),
]
REPOSITORIES = {
    123: [
        repo("my-org/pushable", 1, permissions={"push": True}),
        repo("my-org/admin", 2, permissions={"admin": True}),
        repo("my-org/read-only", 3),
        repo("my-org/old", 4, archived=True, permissions={"push": True}),
    ],
    124: [repo("other/app", 5, permissions={"push": True})],
    125: [repo("other/suspended", 6, permissions={"push": True})],
}


async def fake_api(path, _token, **_kwargs):
    if path == "/user":
        return {"id": 42, "login": "joe-github"}
    if path == "/user/installations":
        return {"installations": INSTALLATIONS}
    if path.startswith("/user/installations/"):
        installation_id = int(path.split("/")[3])
        return {"repositories": REPOSITORIES[installation_id]}
    raise AssertionError(path)


def begin(db, mode="authorize", **claim_changes):
    result = asyncio.run(
        service.begin(db, claims(**claim_changes), BINDING, mode)
    )
    url = result["authorization_url"]
    return url, parse_qs(urlsplit(url).query)["state"][0]


def state_row(db, state):
    return db.get(GitHubOAuthState, hashlib.sha256(state.encode()).hexdigest())


def test_begin_stores_a_hashed_state_and_sends_a_pkce_s256_challenge(db):
    # Act
    url, state = begin(db)

    # Assert
    row = state_row(db, state)
    query = parse_qs(urlsplit(url).query)
    verifier = service.decrypt(row.verifier_ciphertext)["verifier"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert (
        row.consumed_at is None
        and state not in row.state_hash
        and verifier not in url
        and query["code_challenge_method"] == ["S256"]
        and query["code_challenge"] == [expected]
    )


def test_begin_in_install_mode_points_at_the_configured_app(db):
    # Act
    url, _ = begin(db, mode="install")

    # Assert
    assert url.startswith(
        "https://github.com/apps/cnp-portal/installations/new?state="
    )


def test_linking_refuses_to_start_without_a_valid_encryption_key(
    db, monkeypatch
):
    # Arrange
    monkeypatch.setattr(settings, "GITHUB_TOKEN_ENCRYPTION_KEYS", "not-a-key")

    # Act / Assert
    with pytest.raises(service.LinkError) as error:
        begin(db)
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "changed,binding",
    [
        ({"sub": "another-user"}, BINDING),
        ({"sid": "another-session"}, BINDING),
        ({}, "b" * 64),
    ],
)
def test_state_cannot_be_used_by_another_user_session_or_browser(
    db, changed, binding
):
    # Arrange
    _, state = begin(db)

    # Act
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        with pytest.raises(service.LinkError):
            asyncio.run(
                service.callback(db, claims(**changed), state, "code", binding)
            )

    # Assert
    exchange.assert_not_awaited()


def test_callback_links_the_identity_and_forgets_a_previous_installation(db):
    # Arrange
    _, state = begin(db)
    db.add(UserGitHubInstallation(user_sub="joe", installation_id="9"))
    db.commit()

    # Act
    with patch.object(
        service, "exchange", AsyncMock(return_value=tokens())
    ), patch.object(service, "api", fake_api):
        result = asyncio.run(
            service.callback(db, claims(), state, "code", BINDING)
        )

    # Assert
    row = db.get(GitHubOAuthCredential, "cnp-user")
    assert (
        result == {"github_login": "joe-github", "identity_linked": True}
        and ACCESS not in row.credentials_ciphertext
        and db.query(UserGitHubInstallation).count() == 0
    )


def test_a_replayed_callback_never_exchanges_the_code_twice(db):
    # Arrange
    _, state = begin(db)
    exchange = AsyncMock(return_value=tokens())
    with patch.object(service, "exchange", exchange), patch.object(
        service, "api", fake_api
    ):
        asyncio.run(service.callback(db, claims(), state, "code", BINDING))

        # Act
        with pytest.raises(service.LinkError):
            asyncio.run(
                service.callback(db, claims(), state, "code", BINDING)
            )

    # Assert
    assert exchange.await_count == 1


def test_an_expired_state_is_refused(db):
    # Arrange
    _, state = begin(db)
    state_row(db, state).expires_at = service.now() - timedelta(seconds=1)
    db.commit()

    # Act / Assert
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        with pytest.raises(service.LinkError):
            asyncio.run(
                service.callback(db, claims(), state, "code", BINDING)
            )
    exchange.assert_not_awaited()


def test_an_install_callback_continues_with_a_fresh_authorization(db):
    # Arrange
    _, state = begin(db, mode="install")

    # Act
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        result = asyncio.run(
            service.callback(db, claims(), state, "install-code", BINDING)
        )

    # Assert
    exchange.assert_not_awaited()
    assert result["authorization_url"].startswith(service.AUTHORIZE_URL)


def test_tokens_that_do_not_expire_are_refused():
    # Arrange
    response = AsyncMock()
    response.status_code = 200
    response.raise_for_status = lambda: None
    response.json = lambda: {"access_token": ACCESS, "token_type": "bearer"}

    # Act / Assert
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)):
        with pytest.raises(service.LinkError) as error:
            asyncio.run(service.exchange({"code": "c"}))
    assert error.value.status_code == 503


def test_a_rotated_key_still_decrypts_tokens_written_with_the_old_one(
    db, monkeypatch
):
    # Arrange
    old = settings.GITHUB_TOKEN_ENCRYPTION_KEYS
    ciphertext = service.encrypt({"access_token": ACCESS})
    monkeypatch.setattr(
        settings,
        "GITHUB_TOKEN_ENCRYPTION_KEYS",
        f"{Fernet.generate_key().decode()},{old}",
    )

    # Act
    value = service.decrypt(ciphertext)

    # Assert
    assert value == {"access_token": ACCESS}


def test_an_expired_access_token_is_refreshed_once_under_a_lease(db):
    # Arrange
    store(db, access_expires_at=service.now() - timedelta(minutes=1))
    exchange = AsyncMock(return_value=tokens(access_token="new-access"))

    # Act
    with patch.object(service, "exchange", exchange), patch.object(
        service, "api", fake_api
    ):
        proof = asyncio.run(service.fresh_credentials(db, "cnp-user"))

    # Assert
    db.expire_all()
    row = db.get(GitHubOAuthCredential, "cnp-user")
    assert (
        proof["token"] == "new-access"
        and row.version == 2
        and row.refreshing_until is None
    )


def test_a_refresh_already_in_progress_is_not_started_twice(db):
    # Arrange
    store(
        db,
        access_expires_at=service.now() - timedelta(minutes=1),
        refreshing_until=service.now() + timedelta(seconds=60),
    )

    # Act
    with patch.object(service, "exchange", AsyncMock()) as exchange:
        with pytest.raises(service.LinkError) as error:
            asyncio.run(service.fresh_credentials(db, "cnp-user"))

    # Assert
    exchange.assert_not_awaited()
    assert error.value.status_code == 503


def test_a_failed_refresh_releases_its_lease(db):
    # Arrange
    store(db, access_expires_at=service.now() - timedelta(minutes=1))

    # Act
    with patch.object(
        service, "exchange", AsyncMock(side_effect=service.LinkError("x"))
    ):
        with pytest.raises(service.LinkError):
            asyncio.run(service.fresh_credentials(db, "cnp-user"))

    # Assert
    db.expire_all()
    assert db.get(GitHubOAuthCredential, "cnp-user").refreshing_until is None


def test_connect_records_an_installation_the_token_lists(db):
    # Arrange
    store(db)

    # Act
    with patch.object(service, "api", fake_api):
        result = asyncio.run(service.connect(db, claims(), "123"))

    # Assert
    row = db.query(UserGitHubInstallation).one()
    assert (row.user_sub, row.installation_id, result["account"]) == (
        "cnp-user",
        "123",
        "my-org",
    )


@pytest.mark.parametrize("installation_id", ["124", "125", "999"])
def test_connect_refuses_another_app_a_suspended_or_unknown_installation(
    db, installation_id
):
    # Arrange
    store(db)

    # Act / Assert
    with patch.object(service, "api", fake_api):
        with pytest.raises(service.LinkError):
            asyncio.run(service.connect(db, claims(), installation_id))
    assert db.query(UserGitHubInstallation).count() == 0


def test_importable_repositories_keeps_push_or_admin_on_cnp_portal_only(db):
    # Arrange
    store(db)

    # Act
    with patch.object(service, "api", fake_api):
        repos = asyncio.run(service.importable_repositories(db, claims()))

    # Assert
    assert [(r.full_name, r.repo_id, r.installation_id) for r in repos] == [
        ("my-org/pushable", 1, 123),
        ("my-org/admin", 2, 123),
    ]


def test_importable_repositories_is_none_without_a_link(db):
    # Act
    repos = asyncio.run(service.importable_repositories(db, claims()))

    # Assert
    assert repos is None


def test_importable_repositories_is_none_once_the_grant_is_revoked(db):
    # Arrange
    store(db)

    async def revoked(path, _token, **_kwargs):
        raise service.LinkError("revoked")

    # Act
    with patch.object(service, "api", revoked):
        repos = asyncio.run(service.importable_repositories(db, claims()))

    # Assert
    assert repos is None


def test_importable_repositories_reports_a_github_outage(db):
    # Arrange
    store(db)

    async def down(path, _token, **_kwargs):
        raise service.LinkError("down", 503)

    # Act / Assert
    with patch.object(service, "api", down):
        with pytest.raises(github_service.GitHubAppError):
            asyncio.run(service.importable_repositories(db, claims()))


def test_status_endpoint_lists_reachable_installations_without_caching(db):
    # Arrange
    store(db)
    app = FastAPI()
    app.include_router(account.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: claims()
    app.dependency_overrides[get_db] = lambda: db

    # Act
    with patch.object(service, "api", fake_api):
        response = TestClient(app).get("/api/account/github")

    # Assert
    body = response.json()
    assert (
        response.headers["cache-control"] == "no-store"
        and body["identity_linked"] is True
        and [i["id"] for i in body["installations"]] == ["123"]
    )


def test_installation_endpoint_refuses_an_id_the_token_does_not_list(db):
    # Arrange
    store(db)
    app = FastAPI()
    app.include_router(account.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: claims()
    app.dependency_overrides[get_db] = lambda: db

    # Act
    with patch.object(service, "api", fake_api):
        response = TestClient(app).post(
            "/api/account/github-installation",
            json={"installation_id": "999"},
        )

    # Assert
    assert response.status_code == 403


def test_unlink_forgets_the_token_and_the_installation(db):
    # Arrange
    store(db)
    db.add(UserGitHubInstallation(user_sub="cnp-user", installation_id="123"))
    db.commit()

    # Act
    with patch("httpx.AsyncClient.request", AsyncMock()):
        asyncio.run(service.unlink(db, claims()))

    # Assert
    assert (
        db.query(GitHubOAuthCredential).count() == 0
        and db.query(UserGitHubInstallation).count() == 0
    )
