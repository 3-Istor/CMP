import asyncio
import logging
from unittest import mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.routers import deployments as deployments_router
from app.routers import secrets_presence as presence_router
from app.services import keycloak_service
from app.services import secret_presence as sp
from app.services import vault_client
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import (  # noqa: F401
    FakeRepo,
    add_deployment,
    session_factory,
)

PLANTED_VALUE = "hunter2-PLANTED-s3cr3t-value"

BACKEND_YAML = """\
secrets:
  enabled: true
  vaultPath: "project-demo/shop"
  keys:
    - DATABASE_URL
    - STRIPE_KEY
"""

FRONTEND_YAML = """\
secrets:
  keys:
    - PUBLIC_API_KEY
"""

FULLSTACK_FILES = {
    "deploy/values-frontend.yaml": FRONTEND_YAML,
    "deploy/values-backend.yaml": BACKEND_YAML,
}


def vault_answering(status: int, body):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/project-demo/data/shop"
        if isinstance(body, Exception):
            raise body
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return handler


def kv_body(data: dict) -> dict:
    return {"data": {"data": data, "metadata": {"version": 3}}}


@pytest.fixture
def vault(monkeypatch):
    monkeypatch.setattr(settings, "VAULT_URL", "https://vault.test")
    monkeypatch.setattr(settings, "VAULT_TOKEN", "cmp-token")
    real_client = httpx.AsyncClient

    def install(handler):
        def factory(*args, **kwargs):
            return real_client(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(vault_client.httpx, "AsyncClient", factory)

    return install


@pytest.fixture
def client_factory(session_factory):
    started = []

    def build(role, files=None, app_type="fullstack"):
        deployment_id = add_deployment(session_factory, app_type)
        repo = FakeRepo(files or FULLSTACK_FILES)
        app = FastAPI()
        app.include_router(presence_router.router, prefix="/api")

        def override_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: {"sub": "user-1"}
        patches = [
            mock.patch.object(
                deployments_router, "get_file_content", repo.get
            ),
            mock.patch.object(
                deployments_router,
                "get_org_installation_token",
                mock.AsyncMock(return_value="tok"),
            ),
            mock.patch.object(
                keycloak_service, "get_project_role", lambda u, p: role
            ),
        ]
        for patch in patches:
            patch.start()
        started.extend(patches)
        return TestClient(app), deployment_id

    yield build
    for patch in started:
        patch.stop()


def presence_url(deployment_id: int) -> str:
    return f"/api/deployments/{deployment_id}/secrets/presence"


def test_compute_marks_each_expected_key_set_or_missing():
    result = sp.compute(["A", "B"], ["A"])

    assert [(s.name, s.set) for s in result.expected] == [
        ("A", True),
        ("B", False),
    ]


def test_compute_lists_present_keys_that_are_not_expected_as_extra():
    result = sp.compute(["A"], ["Z", "A", "M"])

    assert result.extra == ["M", "Z"]


def test_expected_names_merges_components_and_drops_invalid_names():
    files = [
        {"secrets": {"keys": ["B", "A", "bad name", 3]}},
        {"secrets": {"keys": ["A", "C"]}},
        {"secrets": None},
    ]

    assert sp.expected_names(files) == ["A", "B", "C"]


@pytest.mark.parametrize(
    "secrets,expected",
    [
        (
            {"enabled": True, "vaultPath": "project-x/app"},
            ("project-x", "app"),
        ),
        (
            {"enabled": True, "vaultPath": "kvv2/projects/x/app"},
            ("kvv2", "projects/x/app"),
        ),
        ({"enabled": False, "vaultPath": "project-x/app"}, None),
        ({"enabled": True, "vaultPath": "project-x"}, None),
        ({"enabled": True}, None),
    ],
)
def test_vault_location_follows_the_chart_vault_path(secrets, expected):
    assert sp.vault_location([{"secrets": secrets}], "x") == expected


@pytest.mark.parametrize(
    "vault_path",
    [
        "project-other/app",
        "kvv2/projects/other/app",
        "kvv2/projects/x/../other/app",
        "kvv2/platform/credentials",
        "kvv2/projects/x",
        "kvv2/arcl-cmp/credentials",
    ],
)
def test_a_vault_path_outside_the_apps_project_is_refused(vault_path):
    with pytest.raises(sp.ForeignVaultPathError):
        sp.vault_location(
            [{"secrets": {"enabled": True, "vaultPath": vault_path}}], "x"
        )


def test_presence_reports_missing_and_extra(client_factory, vault):
    vault(
        vault_answering(
            200, kv_body({"DATABASE_URL": PLANTED_VALUE, "LEGACY": "x"})
        )
    )
    client, deployment_id = client_factory("member")

    response = client.get(presence_url(deployment_id))

    assert response.json() == {
        "expected": [
            {"name": "DATABASE_URL", "set": True},
            {"name": "PUBLIC_API_KEY", "set": False},
            {"name": "STRIPE_KEY", "set": False},
        ],
        "extra": ["LEGACY"],
        "vault_path": "project-demo/shop",
    }


def test_presence_treats_a_secret_that_does_not_exist_as_all_missing(
    client_factory, vault
):
    vault(vault_answering(404, {"errors": []}))
    client, deployment_id = client_factory("member")

    response = client.get(presence_url(deployment_id))

    assert all(not s["set"] for s in response.json()["expected"])


def test_presence_allows_project_members(client_factory, vault):
    vault(vault_answering(200, kv_body({})))
    client, deployment_id = client_factory("member")

    assert client.get(presence_url(deployment_id)).status_code == 200


def test_presence_rejects_users_outside_the_project(client_factory, vault):
    vault(vault_answering(200, kv_body({})))
    client, deployment_id = client_factory(None)

    assert client.get(presence_url(deployment_id)).status_code == 403


def test_presence_returns_404_for_an_unknown_app(client_factory):
    client, _ = client_factory("member")

    assert client.get(presence_url(9999)).status_code == 404


def test_presence_returns_503_when_vault_is_unreachable(client_factory, vault):
    vault(vault_answering(200, httpx.ConnectError("connection refused")))
    client, deployment_id = client_factory("member")

    response = client.get(presence_url(deployment_id))

    assert response.status_code == 503
    assert "Vault unreachable" in response.json()["detail"]


def test_presence_returns_503_when_vault_refuses_the_cmp(
    client_factory, vault
):
    vault(vault_answering(403, {"errors": ["permission denied"]}))
    client, deployment_id = client_factory("member")

    assert client.get(presence_url(deployment_id)).status_code == 503


def test_presence_returns_503_when_vault_is_not_configured(
    client_factory, vault, monkeypatch
):
    vault(vault_answering(200, kv_body({})))
    monkeypatch.setattr(settings, "VAULT_TOKEN", "")
    client, deployment_id = client_factory("member")

    assert client.get(presence_url(deployment_id)).status_code == 503


def test_presence_without_vault_secrets_lists_expected_as_missing(
    client_factory,
):
    files = {"deploy/values.yaml": "secrets:\n  keys: [API_KEY]\n"}
    client, deployment_id = client_factory("member", files, "static")

    response = client.get(presence_url(deployment_id))

    assert response.json() == {
        "expected": [{"name": "API_KEY", "set": False}],
        "extra": [],
        "vault_path": None,
    }


def test_presence_response_never_contains_a_secret_value(
    client_factory, vault
):
    vault(
        vault_answering(
            200,
            kv_body({"DATABASE_URL": PLANTED_VALUE, "LEGACY": PLANTED_VALUE}),
        )
    )
    client, deployment_id = client_factory("member")

    response = client.get(presence_url(deployment_id))

    assert PLANTED_VALUE not in response.text


def test_presence_logs_never_contain_a_secret_value(
    client_factory, vault, caplog
):
    vault(vault_answering(200, kv_body({"DATABASE_URL": PLANTED_VALUE})))
    client, deployment_id = client_factory("member")

    with caplog.at_level(logging.DEBUG):
        client.get(presence_url(deployment_id))

    assert PLANTED_VALUE not in caplog.text


@pytest.mark.parametrize("status", [200, 403, 500])
@pytest.mark.parametrize(
    "body_text",
    [f"not json {PLANTED_VALUE}", f'["{PLANTED_VALUE}"]'],
)
def test_read_key_names_errors_never_contain_the_response_body(
    vault, caplog, status, body_text
):
    vault(vault_answering(status, body_text))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(vault_client.VaultError) as raised:
            asyncio.run(vault_client.read_key_names("project-demo", "shop"))

    assert PLANTED_VALUE not in str(raised.value)
    assert PLANTED_VALUE not in repr(raised.value.__context__)
    assert PLANTED_VALUE not in caplog.text


def test_read_key_names_returns_only_sorted_names(vault):
    vault(vault_answering(200, kv_body({"B": PLANTED_VALUE, "A": "1"})))

    names = asyncio.run(vault_client.read_key_names("project-demo", "shop"))

    assert names == ["A", "B"]


def test_503_detail_never_contains_a_secret_value(client_factory, vault):
    vault(vault_answering(500, f'{{"errors": ["{PLANTED_VALUE}"]}}'))
    client, deployment_id = client_factory("member")

    response = client.get(presence_url(deployment_id))

    assert PLANTED_VALUE not in response.text
