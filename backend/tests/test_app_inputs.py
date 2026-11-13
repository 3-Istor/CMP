import json
import subprocess
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, TargetCloud
from app.routers import deployments as router
from app.schemas.catalog import CatalogField, CatalogTemplate
from app.services import terraform_orchestrator as orchestrator
from app.services.clusters import state
from app.services.keycloak_service import get_current_user
from app.services.terraform_executor import TerraformExecutor
from tests.test_app_security_data import session_factory  # noqa: F401

# k3s-gitops-app/manifest.json of app-templates.
MANIFEST_VARIABLES = [
    {
        "name": "template_repo_name",
        "label": "Stack Type (Git Template)",
        "type": "select",
        "options": [
            "template-html-css",
            "template-app-webapp-python-fastapi-react",
        ],
        "required": True,
    },
    {
        "name": "github_owner",
        "label": "GitHub Organization/User",
        "type": "text",
        "default": "3-Istor",
        "required": True,
    },
]

TEMPLATE = CatalogTemplate(
    id="k3s-gitops-app",
    name="Kubernetes App (GitOps)",
    description="",
    icon="",
    category="paas",
    fields=[CatalogField(**v) for v in MANIFEST_VARIABLES],
)

PORTAL_PAYLOAD = {
    "template_repo_name": "template-html-css",
    "github_owner": "3-Istor",
    "project_name": "demo",
    "github_installation_id": "12345",
}


def _create(session_factory, app_config):  # noqa: F811
    with session_factory() as db:
        db.add(
            Project(
                project_name="demo",
                owner_username="u",
                target_cloud=TargetCloud.ONPREM,
            )
        )
        db.commit()
    app = FastAPI()
    app.include_router(router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    with (
        mock.patch.object(router, "is_cnp_admin", lambda token: True),
        mock.patch.object(router, "get_template_by_id", lambda _id: TEMPLATE),
        mock.patch.object(router, "ensure_app_names_free", mock.AsyncMock()),
        mock.patch.object(router.terraform_orchestrator, "run_deployment"),
        mock.patch.object(state, "fresh_state", lambda cloud: "up"),
    ):
        return TestClient(app).post(
            "/api/deployments/",
            json={
                "name": "shop",
                "template_id": "k3s-gitops-app",
                "project_id": "demo",
                "app_config": app_config,
            },
        )


def test_create_refuses_a_key_the_manifest_does_not_list(
    session_factory,  # noqa: F811
):
    # Arrange
    config = {**PORTAL_PAYLOAD, "replica_count": 3}

    # Act
    response = _create(session_factory, config)

    # Assert
    assert response.status_code == 422


@pytest.mark.parametrize(
    "key,value",
    [
        ("vault_url", "https://attacker.example"),
        ("keycloak_url", "https://attacker.example"),
        ("keycloak_realm_id", "other-project"),
        ("vault_token", "x"),
        ("target_cloud", "aws"),
        ("github_owner", "attacker-org"),
    ],
)
def test_create_refuses_a_platform_input(
    session_factory, key, value  # noqa: F811
):
    # Arrange
    config = {**PORTAL_PAYLOAD, key: value}

    # Act
    response = _create(session_factory, config)

    # Assert
    assert response.status_code == 422


@pytest.mark.parametrize(
    "value",
    ["template-html-css; rm -rf /", "anything-else", ["template-html-css"]],
)
def test_create_refuses_a_select_value_outside_its_options(
    session_factory, value  # noqa: F811
):
    # Arrange
    config = {**PORTAL_PAYLOAD, "template_repo_name": value}

    # Act
    response = _create(session_factory, config)

    # Assert
    assert response.status_code == 422


def test_create_accepts_the_portal_payload_and_stores_server_values(
    session_factory,  # noqa: F811
):
    # Arrange
    config = dict(PORTAL_PAYLOAD)

    # Act
    response = _create(session_factory, config)

    # Assert
    assert response.status_code == 202
    with session_factory() as db:
        stored = json.loads(db.query(Deployment).one().app_config)
    assert stored == {
        "template_repo_name": "template-html-css",
        "github_owner": "3-Istor",
        "project_name": "demo",
        "target_cloud": "onprem",
    }


def test_a_legacy_row_with_platform_overrides_does_not_reach_terraform_argv(
    session_factory, tmp_path, monkeypatch  # noqa: F811
):
    # Arrange
    monkeypatch.chdir(tmp_path)
    with session_factory() as db:
        db.add(
            Deployment(
                id=7,
                name="shop",
                template_id="k3s-gitops-app",
                provider_type=ProviderType.KUBERNETES,
                project_id="demo",
                status=DeploymentStatus.PENDING,
                app_config=json.dumps(
                    {
                        "template_repo_name": "template-html-css",
                        "github_owner": "attacker-org",
                        "project_name": "victim",
                        "target_cloud": "onprem",
                        "github_installation_id": "12345",
                        "keycloak_realm_id": "victim-staging",
                        "keycloak_url": "https://attacker.example",
                        "vault_url": "https://attacker.example",
                    }
                ),
            )
        )
        db.commit()
    repo = mock.Mock()
    repo.get_template_by_id.return_value = {
        "name": "Kubernetes App (GitOps)",
        "variables": MANIFEST_VARIABLES,
        "_template_path": str(tmp_path),
    }
    commands: list[list[str]] = []

    def run(self, command, capture_output=True, stream_output=False):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}")

    # Act
    with (
        mock.patch.object(orchestrator, "SessionLocal", session_factory),
        mock.patch.object(orchestrator, "get_repository", lambda: repo),
        mock.patch.object(
            orchestrator,
            "get_org_installation_token",
            mock.AsyncMock(return_value="ghs_token"),
        ),
        mock.patch.object(orchestrator, "_notify"),
        mock.patch.object(orchestrator.customer_identity, "apply_if_enabled"),
        mock.patch.object(TerraformExecutor, "init"),
        mock.patch.object(TerraformExecutor, "get_state_summary", dict),
        mock.patch.object(TerraformExecutor, "_run_command", run),
    ):
        orchestrator.run_deployment(7)

    # Assert
    var_args = {
        a.split("=", 1)[0]: a.split("=", 1)[1]
        for c in commands
        if c[1] in ("plan", "apply")
        for a in c
        if "=" in a and not a.startswith("-")
    }
    assert var_args == {
        "template_repo_name": "template-html-css",
        "github_owner": "3-Istor",
        "app_name": "shop",
        "project_name": "demo",
        "target_cloud": "onprem",
        "github_token": "ghs_token",
    }
