"""Operator preflight rejects incomplete state and never pretends to perform OAuth."""

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import repository_imports as imports

SPEC = importlib.util.spec_from_file_location(
    "cnp_import_readiness",
    Path(__file__).resolve().parents[2] / "scripts/import-readiness.py",
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


@pytest.mark.parametrize(
    "enabled,bucket,region",
    [
        (False, "recipe-state", "eu-west-3"),
        (True, "", "eu-west-3"),
        (True, "   ", "eu-west-3"),
        (True, "recipe-state", ""),
    ],
)
def test_import_never_falls_back_to_local_state(
    monkeypatch, enabled, bucket, region
):
    monkeypatch.setattr(settings, "IMPORT_ENABLED", True)
    monkeypatch.setattr(settings, "TF_BACKEND_S3_ENABLED", enabled)
    monkeypatch.setattr(settings, "TF_BACKEND_S3_BUCKET", bucket)
    monkeypatch.setattr(settings, "TF_BACKEND_AWS_REGION", region)
    with pytest.raises(HTTPException) as error:
        imports.enabled()
    assert error.value.status_code == 503
    assert "S3" in error.value.detail
    checks = preflight.configuration_checks()
    assert not all(checks[key] for key in imports.import_state_configuration())


def test_missing_configuration_prevents_every_connected_check(monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_DISPATCH_WORKFLOW", "typo.yml")
    monkeypatch.setattr(
        settings, "IMPORT_IMAGE_PREFIX", "other-registry.invalid/app"
    )
    checks = preflight.configuration_checks()
    assert checks["IMPORT_DISPATCH_WORKFLOW"] is False
    assert checks["IMPORT_IMAGE_PREFIX"] is False
    monkeypatch.setattr(
        preflight,
        "configuration_checks",
        lambda: {"TF_BACKEND_S3_BUCKET": False},
    )
    connected = AsyncMock()
    monkeypatch.setattr(preflight, "live_checks", connected)
    assert preflight.main(["--live"]) == 1
    connected.assert_not_called()


def test_github_check_does_not_require_published_ci(monkeypatch, capsys):
    static = Mock(
        side_effect=AssertionError(
            "CI configuration must not be required here"
        )
    )
    monkeypatch.setattr(preflight, "configuration_checks", static)
    monkeypatch.setattr(
        preflight,
        "github_linking_checks",
        AsyncMock(return_value={"KEYCLOAK_GITHUB_PROVIDER": False}),
    )
    monkeypatch.setattr(settings, "IMPORT_ENABLED", False)
    assert preflight.main(["--github"]) == 1
    assert "MISSING KEYCLOAK_GITHUB_PROVIDER" in capsys.readouterr().out
    assert settings.IMPORT_ENABLED is False
    static.assert_not_called()


def test_remote_error_body_cannot_leak_to_console(monkeypatch, capsys):
    marker = "private-upstream-error-value"
    monkeypatch.setattr(
        preflight,
        "github_linking_checks",
        AsyncMock(side_effect=HTTPException(403, marker)),
    )
    assert preflight.main(["--github"]) == 1
    output = capsys.readouterr().out
    assert marker not in output
    assert "STOP" in output


@pytest.mark.parametrize(
    "provider_change",
    [
        None,
        {"enabled": False},
        {"linkOnly": False},
        {"trustEmail": True},
        {"storeToken": True},
        {},
    ],
)
def test_provider_configuration_must_match_linking_contract(
    monkeypatch, provider_change
):
    provider = {
        "alias": "github",
        "providerId": "github",
        "enabled": True,
        "linkOnly": True,
        "trustEmail": False,
        "storeToken": False,
        "config": {"clientId": "public-client-id", "clientSecret": "masked"},
    }
    providers = (
        [] if provider_change is None else [{**provider, **provider_change}]
    )
    response = Mock()
    response.json.return_value = providers
    client = AsyncMock()
    client.get.return_value = response
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=client)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(preflight.httpx, "AsyncClient", factory)
    monkeypatch.setattr(
        preflight.keycloak_service, "_get_admin_token", lambda: "test-only"
    )
    monkeypatch.setattr(preflight, "generate_jwt", lambda: "test-only-app-jwt")
    github = AsyncMock(
        return_value={
            "slug": "cnp-portal",
            "owner": {"login": "3-Istor"},
            "permissions": {"emails": "read"},
        }
    )
    monkeypatch.setattr(preflight, "github", github)
    monkeypatch.setattr(
        settings,
        "GITHUB_ACCOUNT_LINK_URL",
        "https://sso.example.org/realms/test/account/",
    )
    checks = asyncio.run(preflight.github_linking_checks())
    assert all(checks.values()) is (provider_change == {})
    github.assert_awaited_once_with("GET", "/app", "test-only-app-jwt")
    assert client.get.call_args.args[0].endswith(
        "/identity-provider/instances"
    )


def test_unavailable_chart_prevents_argo_and_vault_checks(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "github_linking_checks",
        AsyncMock(
            side_effect=AssertionError(
                "IdP administration is not required here"
            )
        ),
    )
    monkeypatch.setattr(
        preflight,
        "require_workflow_configuration",
        AsyncMock(return_value="read-only-metadata"),
    )
    monkeypatch.setattr(
        preflight, "github", AsyncMock(return_value={"sha": "b" * 40})
    )
    monkeypatch.setattr(settings, "IMPORT_CHART_SHA", "a" * 40)
    argo = AsyncMock()
    monkeypatch.setattr(preflight, "require_gitops_consumer", argo)
    with pytest.raises(HTTPException) as error:
        asyncio.run(preflight.live_checks())
    assert error.value.status_code == 503
    argo.assert_not_awaited()

    preflight.github_linking_checks.assert_not_awaited()
