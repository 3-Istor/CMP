"""Runtime secrets must stay out of the imported repo and the CI build job."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.routers.repository_imports import ImportRequest
from app.services import repository_imports as service


def request(**changes):
    return ImportRequest(
        project_name="sandbox",
        app_name="session-manager",
        repository_url="https://github.com/example/sessions",
        port=8000,
        **changes
    )


def test_runtime_and_smoke_configuration_remain_distinct():
    payload = request(
        runtime_environment={"AUTH_MODE": "google"},
        smoke_environment={"APP_SECRET": "__CNP_RANDOM_SECRET__"},
        runtime_secret_keys=["APP_SECRET"],
    )
    assert payload.runtime_environment == {"AUTH_MODE": "google"}
    assert payload.runtime_secret_keys == ["APP_SECRET"]


@pytest.mark.parametrize(
    "changes",
    [
        {"runtime_environment": {"APP_SECRET": "do-not-commit"}},
        {
            "runtime_environment": {
                "DATABASE_URL": "postgres://user:password@example.org/db"
            }
        },
        {"runtime_environment": {"KEY": "one\ntwo"}},
        {
            "smoke_environment": {
                "GOOGLE_CLIENT_SECRET": "a-real-secret-must-not-go-here"
            }
        },
        {"runtime_secret_keys": ["APP_SECRET"]},
        {"runtime_secret_keys": ["APP_SECRET", "APP_SECRET"]},
        {
            "runtime_secret_keys": ["APP_SECRET"],
            "smoke_environment": {"APP_SECRET": "real"},
        },
        {
            "runtime_environment": {"PUBLIC": "x"},
            "runtime_secret_keys": ["PUBLIC"],
            "smoke_environment": {"PUBLIC": "cnp-smoke-only"},
        },
    ],
)
def test_invalid_runtime_contract_is_refused_before_dispatch(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_missing_vault_secret_stops_the_grant():
    client = AsyncMock()
    client.get.return_value = Mock(status_code=404)
    with patch.object(
        settings, "VAULT_URL", "https://vault.example.org"
    ), patch.object(settings, "VAULT_TOKEN", "test"), patch.object(
        service.httpx, "AsyncClient"
    ) as factory:
        factory.return_value.__aenter__.return_value = client
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                service.require_runtime_secrets(
                    SimpleNamespace(
                        project_name="sandbox",
                        app_name="web",
                        configuration={"runtime_secret_keys": ["APP_SECRET"]},
                    )
                )
            )
    assert error.value.status_code == 422


def test_vault_values_are_not_returned_to_the_worker():
    response = Mock(status_code=200)
    response.json.return_value = {
        "data": {"data": {"APP_SECRET": "private-app-secret"}}
    }
    client = AsyncMock()
    client.get.return_value = response
    with patch.object(
        settings, "VAULT_URL", "https://vault.example.org"
    ), patch.object(settings, "VAULT_TOKEN", "test"), patch.object(
        service.httpx, "AsyncClient"
    ) as factory:
        factory.return_value.__aenter__.return_value = client
        result = asyncio.run(
            service.require_runtime_secrets(
                SimpleNamespace(
                    project_name="sandbox",
                    app_name="web",
                    configuration={"runtime_secret_keys": ["APP_SECRET"]},
                )
            )
        )
    assert result is None
    assert (
        client.get.call_args.args[0]
        == "https://vault.example.org/v1/project-sandbox/data/web/runtime"
    )


@pytest.mark.parametrize(
    "branch",
    ["../main", "-main", "topic//main", "topic.lock", "topic/", "x\nmain"],
)
def test_invalid_source_branch_is_refused_before_dispatch(branch):
    with pytest.raises(ValidationError):
        request(source_branch=branch)


def test_branch_recipe_refuses_other_projects_before_database_access():
    db = Mock()
    with patch.object(settings, "IMPORT_ALLOWED_PROJECT", "joetest"):
        with pytest.raises(HTTPException) as error:
            service.fresh_project(db, "production", "user", "joe")
    assert error.value.status_code == 403
    db.query.assert_not_called()


@pytest.mark.parametrize("ready", [False, True])
def test_registry_contract_must_support_import_provenance(ready):
    import base64
    import json

    properties = (
        {
            name: {}
            for name in [
                "sourceRevision",
                "chartRevision",
                "imageTag",
                "imageDigest",
            ]
        }
        if ready
        else {}
    )
    schema = {
        "properties": {
            "spec": {
                "properties": {"apps": {"items": {"properties": properties}}}
            }
        }
    }
    contents = base64.b64encode(json.dumps(schema).encode()).decode()
    with patch.object(
        service, "get_installation_token", AsyncMock(return_value="test")
    ), patch.object(
        service, "github", AsyncMock(return_value={"content": contents})
    ):
        if ready:
            asyncio.run(service.require_registry_contract())
        else:
            with pytest.raises(HTTPException) as error:
                asyncio.run(service.require_registry_contract())
            assert error.value.status_code == 503


def test_wrong_callback_prevents_dispatch():
    github = AsyncMock(
        side_effect=[
            {"sha": "a" * 40},
            {"value": "https://another-cmp.example.org/api"},
        ]
    )
    with patch.object(settings, "IMPORT_WORKFLOW_SHA", "a" * 40), patch.object(
        settings, "IMPORT_CALLBACK_URL", "https://recipe.example.org/api"
    ), patch.object(
        service, "get_installation_token", AsyncMock(return_value="test")
    ), patch.object(
        service, "github", github
    ):
        with pytest.raises(HTTPException):
            asyncio.run(service.dispatch(SimpleNamespace(id="test-import")))
    assert all(call.args[0] != "POST" for call in github.call_args_list)


def test_dispatch_uses_the_existing_driver_with_import_id():
    github = AsyncMock(
        side_effect=[
            {"sha": "a" * 40},
            {"value": "https://recipe.example.org/api"},
            {"value": settings.IMPORT_OIDC_AUDIENCE},
            {},
        ]
    )
    with patch.object(settings, "IMPORT_WORKFLOW_SHA", "a" * 40), patch.object(
        settings, "IMPORT_CALLBACK_URL", "https://recipe.example.org/api"
    ), patch.object(
        service, "get_installation_token", AsyncMock(return_value="test")
    ), patch.object(
        service, "github", github
    ):
        asyncio.run(service.dispatch(SimpleNamespace(id="test-import")))
    assert github.call_args.args[1].endswith(
        "/actions/workflows/docker-image.yml/dispatches"
    )
    assert github.call_args.kwargs["json"]["inputs"] == {
        "import_id": "test-import"
    }


@pytest.mark.parametrize(
    "allowed_project,branch,blocked",
    [
        ("joetest", "import-fichier-joe-raph", True),
        ("", "main", False),
        ("joetest", "main", False),
    ],
)
def test_recipe_mode_blocks_unrelated_catalog_and_project_mutations(
    allowed_project, branch, blocked
):
    from app.services.import_policy import require_standard_platform_mode

    with patch.object(
        settings, "IMPORT_ALLOWED_PROJECT", allowed_project
    ), patch.object(settings, "CNP_REGISTRY_BRANCH", branch):
        if blocked:
            with pytest.raises(HTTPException) as error:
                require_standard_platform_mode()
            assert error.value.status_code == 409
        else:
            require_standard_platform_mode()


@pytest.mark.parametrize(
    "protected,approved_revision", [(True, True), (False, True), (True, False)]
)
def test_recipe_refuses_unprotected_or_unreviewed_image_updater(
    protected, approved_revision
):
    import base64

    revision = "b" * 40
    expression = (
        '{{ if eq .metadata.name "joetest" }}'
        + revision
        + "{{ else }}HEAD{{ end }}"
    )
    response = Mock()
    response.json.return_value = {
        "metadata": {
            "annotations": {
                "cnp.3istor.com/import-test-k3s-revision": revision
            }
        },
        "spec": {
            "template": {
                "spec": {
                    "source": {
                        "repoURL": "https://github.com/3-Istor/K3s",
                        "path": "k8s/helm/cnp-image-updaters",
                        "targetRevision": (
                            expression if approved_revision else "HEAD"
                        ),
                    }
                }
            }
        },
    }
    client = AsyncMock()
    client.get.return_value = response
    template = (
        '{{ if not (hasKey $app "sourceRevision") }}'
        if protected
        else "{{ range .Values.apps }}"
    )
    with patch.object(
        settings, "IMPORT_ALLOWED_PROJECT", "joetest"
    ), patch.object(
        settings, "CNP_REGISTRY_BRANCH", "import-fichier-joe-raph"
    ), patch.object(
        service.httpx, "AsyncClient"
    ) as factory, patch.object(
        service, "get_installation_token", AsyncMock(return_value="test")
    ), patch.object(
        service,
        "github",
        AsyncMock(
            return_value={
                "content": base64.b64encode(template.encode()).decode()
            }
        ),
    ) as github:
        factory.return_value.__aenter__.return_value = client
        if protected and approved_revision:
            asyncio.run(service.require_image_updater_consumer())
            assert github.call_args.kwargs["params"] == {"ref": revision}
        else:
            with pytest.raises(HTTPException) as error:
                asyncio.run(service.require_image_updater_consumer())
            assert error.value.status_code == 503


def test_head_encodes_the_selected_source_branch():
    job = SimpleNamespace(
        repository_url="https://github.com/example/app",
        configuration={"source_branch": "recipe/joe"},
    )
    with patch.object(
        service, "github", AsyncMock(return_value={"sha": "c" * 40})
    ) as github:
        assert asyncio.run(service.head(job, "test")) == "c" * 40
    assert (
        github.call_args.args[1] == "/repos/example/app/commits/recipe%2Fjoe"
    )


@pytest.mark.parametrize("status", [409, 503])
def test_stopped_or_unverifiable_cluster_stops_import_before_github_grants(
    status,
):
    from datetime import datetime, timedelta, timezone

    from app.models.project import TargetCloud

    db = Mock()
    db.query.return_value.filter.return_value.one_or_none.return_value = (
        SimpleNamespace(installation_id="11")
    )
    job = SimpleNamespace(
        project_name="sandbox",
        app_name="web",
        user_sub="user",
        username="joe",
        installation_id="11",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1),
        configuration={"target_cloud": "aws", "chart_sha": "b" * 40},
    )
    with patch.object(service, "enabled"), patch.object(
        settings, "IMPORT_CHART_SHA", "b" * 40
    ), patch.object(
        service,
        "fresh_project",
        return_value=SimpleNamespace(target_cloud=TargetCloud.AWS),
    ), patch.object(
        service.project_registry,
        "read_record",
        AsyncMock(
            return_value=(
                {"spec": {"status": "active", "targetCloud": "aws"}},
                "test",
            )
        ),
    ), patch.object(
        service,
        "ensure_cluster_up",
        AsyncMock(side_effect=HTTPException(status, "cluster unavailable")),
    ) as cluster, patch.object(
        service, "require_gitops_consumer", AsyncMock()
    ) as gitops, patch.object(
        service, "scoped_token", AsyncMock()
    ) as token:
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.authorize(db, job))
    assert error.value.status_code == status
    cluster.assert_awaited_once_with(TargetCloud.AWS)
    gitops.assert_not_awaited()
    token.assert_not_awaited()
