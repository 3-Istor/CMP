import asyncio
import json
import subprocess
from unittest import mock

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.user_github import UserGitHubInstallation
from app.routers import account as account_router
from app.routers import deployments as deployments_router
from app.routers import security as security_router
from app.schemas.account import GitHubInstallationRequest
from app.services import github_service
from app.services import terraform_orchestrator as orchestrator
from app.services.environments import gitops
from app.services.terraform_executor import TerraformExecutor
from tests.test_app_inputs import MANIFEST_VARIABLES, PORTAL_PAYLOAD
from tests.test_app_inputs import _create as create_deployment
from tests.test_app_security_data import session_factory  # noqa: F401

ORG_INSTALLATION = "111"
FOREIGN_INSTALLATION = "999"


@pytest.fixture(autouse=True)
def org_installation():
    with mock.patch.object(
        settings, "GITHUB_INSTALLATION_ID", ORG_INSTALLATION
    ):
        yield


def test_client_installation_id_is_accepted_but_not_stored(
    session_factory,  # noqa: F811
):
    # Arrange
    config = {**PORTAL_PAYLOAD, "github_installation_id": FOREIGN_INSTALLATION}

    # Act
    response = create_deployment(session_factory, config)

    # Assert
    assert response.status_code == 202
    with session_factory() as db:
        stored = json.loads(db.query(Deployment).one().app_config)
    assert "github_installation_id" not in stored


def _foreign_installation_row(**overrides):
    return Deployment(
        **{
            "id": 7,
            "name": "shop",
            "template_id": "k3s-gitops-app",
            "provider_type": ProviderType.KUBERNETES,
            "project_id": "demo",
            "status": DeploymentStatus.RUNNING,
            "github_repo_url": "https://github.com/3-Istor/shop",
            "app_config": json.dumps(
                {
                    "template_repo_name": "template-html-css",
                    "github_installation_id": FOREIGN_INSTALLATION,
                }
            ),
            **overrides,
        }
    )


def test_terraform_token_is_minted_on_the_org_installation(
    session_factory, tmp_path, monkeypatch  # noqa: F811
):
    # Arrange
    monkeypatch.chdir(tmp_path)
    with session_factory() as db:
        db.add(_foreign_installation_row(status=DeploymentStatus.PENDING))
        db.commit()
    repo = mock.Mock()
    repo.get_template_by_id.return_value = {
        "variables": MANIFEST_VARIABLES,
        "_template_path": str(tmp_path),
    }
    minted = mock.AsyncMock(return_value="ghs_org")

    def run(self, command, capture_output=True, stream_output=False):
        return subprocess.CompletedProcess(command, 0, stdout="{}")

    # Act
    with (
        mock.patch.object(orchestrator, "SessionLocal", session_factory),
        mock.patch.object(orchestrator, "get_repository", lambda: repo),
        mock.patch.object(github_service, "get_installation_token", minted),
        mock.patch.object(orchestrator, "_notify"),
        mock.patch.object(orchestrator.customer_identity, "apply_if_enabled"),
        mock.patch.object(TerraformExecutor, "init"),
        mock.patch.object(TerraformExecutor, "get_state_summary", dict),
        mock.patch.object(TerraformExecutor, "_run_command", run),
    ):
        orchestrator.run_deployment(7)

    # Assert
    minted.assert_awaited_once_with(ORG_INSTALLATION)


@pytest.mark.parametrize(
    "mint",
    [
        deployments_router._get_github_token_for_deployment,
        gitops.token_for,
        security_router._github_token,
    ],
    ids=["deployments", "gitops", "security"],
)
def test_day2_tokens_use_the_org_installation_not_the_stored_one(mint):
    minted = mock.AsyncMock(return_value="tok")

    with mock.patch.object(github_service, "get_installation_token", minted):
        asyncio.run(mint(_foreign_installation_row()))

    minted.assert_awaited_once_with(ORG_INSTALLATION)


class TestRecordInstallation:
    @pytest.fixture
    def db(self, session_factory):  # noqa: F811
        with session_factory() as session:
            yield session

    def _save(self, db, installation_id):
        return asyncio.run(
            account_router.save_github_installation(
                GitHubInstallationRequest(installation_id=installation_id),
                {"preferred_username": "alice"},
                db,
            )
        )

    def test_foreign_installation_is_refused_with_409(self, db):
        with pytest.raises(HTTPException) as exc:
            self._save(db, FOREIGN_INSTALLATION)

        assert exc.value.status_code == 409
        assert "CNP-241" in exc.value.detail

    def test_foreign_installation_is_not_stored(self, db):
        with pytest.raises(HTTPException):
            self._save(db, FOREIGN_INSTALLATION)

        assert db.query(UserGitHubInstallation).count() == 0

    def test_org_installation_is_recorded(self, db):
        self._save(db, ORG_INSTALLATION)

        assert (
            db.query(UserGitHubInstallation).one().installation_id
            == ORG_INSTALLATION
        )

    def test_nothing_is_accepted_when_the_org_installation_is_unset(self, db):
        with mock.patch.object(settings, "GITHUB_INSTALLATION_ID", ""):
            with pytest.raises(HTTPException) as exc:
                self._save(db, "")

        assert exc.value.status_code == 400


class TestScopedInstallationToken:
    def _mint(self, *args):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(201, json={"token": "ghs_scoped"})

        real_client = httpx.AsyncClient
        with (
            mock.patch.object(github_service, "generate_jwt", lambda: "jwt"),
            mock.patch.object(
                github_service.httpx,
                "AsyncClient",
                lambda **kw: real_client(
                    transport=httpx.MockTransport(handler)
                ),
            ),
        ):
            token = asyncio.run(
                github_service.get_scoped_installation_token(*args)
            )
        return token, requests

    def test_request_names_the_repositories_and_permissions(self):
        _, requests = self._mint(
            "42", ["shop"], {"contents": "read", "metadata": "read"}
        )

        assert json.loads(requests[0].content) == {
            "repositories": ["shop"],
            "permissions": {"contents": "read", "metadata": "read"},
        }

    def test_request_targets_the_installation(self):
        _, requests = self._mint("42", ["shop"], {"contents": "read"})

        assert requests[0].url.path == "/app/installations/42/access_tokens"

    def test_returns_the_minted_token(self):
        token, _ = self._mint("42", ["shop"], {"contents": "read"})

        assert token == "ghs_scoped"

    @pytest.mark.parametrize(
        "repositories,permissions",
        [
            ([], {"contents": "read"}),
            (["shop"], {}),
            (["owner/shop"], {"contents": "read"}),
            (["shop"], {"contents": "admin"}),
        ],
        ids=["no-repo", "no-permission", "owner-in-name", "bad-level"],
    )
    def test_unscoped_requests_are_refused_before_calling_github(
        self, repositories, permissions
    ):
        with mock.patch.object(
            github_service, "_request_installation_token"
        ) as request:
            with pytest.raises(github_service.GitHubAppError):
                asyncio.run(
                    github_service.get_scoped_installation_token(
                        "42", repositories, permissions
                    )
                )

        request.assert_not_called()
