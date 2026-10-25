"""Local import UI keeps real auth, reviewed source and branch selection."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.routers import repository_imports as router
from app.services import repository_imports as service
from app.services.keycloak_service import get_current_user
from app.services.template_repository import TemplateRepository

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def portal_claims(**changes):
    now = datetime.now(timezone.utc)
    return {
        "iss": f"{settings.KEYCLOAK_URL.rstrip('/')}/realms/{settings.KEYCLOAK_REALM}",
        "aud": settings.IMPORT_USER_AUDIENCE,
        "azp": settings.KEYCLOAK_CLIENT_ID,
        "preferred_username": "joe",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **changes,
    }


def verify(claims):
    key_client = Mock()
    key_client.get_signing_key_from_jwt.return_value = SimpleNamespace(
        key=SIGNING_KEY.public_key()
    )
    with patch.object(service, "enabled"), patch.object(
        service, "jwks", return_value=key_client
    ):
        return service.verify_token(
            jwt.encode(claims, SIGNING_KEY, algorithm="RS256")
        )


def test_verified_portal_token_without_sub_resolves_the_live_identity():
    with patch.object(
        service.keycloak_service,
        "get_user_id_from_token",
        return_value="user-id",
    ) as resolve:
        claims = verify(portal_claims())
    assert claims["sub"] == "user-id"
    resolve.assert_called_once()


@pytest.mark.parametrize(
    "changes",
    [
        {"azp": "another-client"},
        {"azp": None},
        {"aud": "another-audience"},
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        {"iss": "https://untrusted.test/realms/3istor"},
        {"preferred_username": ""},
    ],
)
def test_untrusted_portal_tokens_never_trigger_identity_lookup(changes):
    with patch.object(
        service.keycloak_service, "get_user_id_from_token"
    ) as resolve:
        with pytest.raises(HTTPException) as error:
            verify(portal_claims(**changes))
    assert error.value.status_code == 401
    resolve.assert_not_called()


def test_unavailable_identity_lookup_stops_the_verified_request():
    with patch.object(
        service.keycloak_service,
        "get_user_id_from_token",
        side_effect=HTTPException(503, "Keycloak unavailable"),
    ):
        with pytest.raises(HTTPException) as error:
            verify(portal_claims())
    assert error.value.status_code == 503


def test_import_availability_is_authenticated_even_when_disabled():
    api = FastAPI()
    api.include_router(router.router, prefix="/api")
    with TestClient(api) as client:
        assert client.get(
            "/api/repository-imports/availability"
        ).status_code in (401, 403)


@pytest.mark.parametrize(
    "enabled,configured,ready",
    [(False, False, False), (True, False, False), (True, True, True)],
)
def test_availability_reports_readiness_without_source_or_ci_access(
    enabled, configured, ready
):
    api = FastAPI()
    api.include_router(router.router, prefix="/api")
    api.dependency_overrides[get_current_user] = lambda: {"sub": "signed-user"}
    with patch.object(settings, "IMPORT_ENABLED", enabled), patch.object(
        service,
        "enabled",
        side_effect=None if configured else HTTPException(503, "missing"),
    ), patch.object(service, "github", AsyncMock()) as github:
        with TestClient(api) as client:
            response = client.get("/api/repository-imports/availability")
    assert response.status_code == 200
    assert response.json()["ready"] is ready
    github.assert_not_called()


def test_local_catalog_reads_the_selected_branch_without_clone_or_pull(
    tmp_path,
):
    templates = tmp_path / "templates"
    templates.mkdir()
    app = templates / "local-app"
    app.mkdir()
    (app / "manifest.json").write_text('{"id":"local-app","enabled":true}')
    with patch.object(
        settings, "TEMPLATE_REPOSITORY_PATH", str(tmp_path)
    ), patch("app.services.template_repository.git.Repo") as git_repo:
        repo = TemplateRepository()
        assert repo.get_available_templates()[0]["id"] == "local-app"
        repo.force_sync()
    git_repo.assert_not_called()


def test_missing_local_catalog_stops_instead_of_cloning_main(tmp_path):
    with patch.object(
        settings, "TEMPLATE_REPOSITORY_PATH", str(tmp_path)
    ), patch("app.services.template_repository.git.Repo") as git_repo:
        with pytest.raises(ValueError):
            TemplateRepository()
    git_repo.assert_not_called()
