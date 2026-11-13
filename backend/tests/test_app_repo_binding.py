import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import mcp_server
from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project
from app.routers import deployments as router
from app.services import app_repo, terraform_orchestrator
from app.services.agents import internal_api
from app.services.agents import repo as repo_ops
from app.services.clusters import state
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import session_factory  # noqa: F401

SHOP_URL = "https://github.com/3-Istor/shop"


def test_running_app_resolves_to_its_own_repository():
    repo = app_repo.bound_repo(SHOP_URL, "shop", "running")

    assert repo == "3-Istor/shop"


@pytest.mark.parametrize(
    "url,name,status",
    [
        (SHOP_URL, "shop", DeploymentStatus.FAILED),
        (SHOP_URL, "shop", DeploymentStatus.DELETED),
        (SHOP_URL, "shop", DeploymentStatus.DEPLOYING),
        ("https://github.com/someone-else/shop", "shop", "running"),
        ("https://github.com/3-Istor/cnp-projects", "shop", "running"),
        ("https://github.com/3-Istor/cnp-projects", "cnp-projects", "running"),
        (None, "shop", "running"),
    ],
    ids=[
        "failed",
        "deleted",
        "deploying",
        "foreign-owner",
        "other-repo",
        "platform-repo",
        "no-url",
    ],
)
def test_repository_is_refused_unless_bound_to_a_running_app(
    url, name, status
):
    with pytest.raises(app_repo.RepoNotBound):
        app_repo.bound_repo(url, name, status)


@pytest.fixture
def mcp_caller(monkeypatch):
    """An MCP caller whose role on the project is set per test."""
    caller = SimpleNamespace(
        role=ProjectRole.MEMBER,
        app={},
        claims={"cnp_agent": {"client": "test"}},
        username="alice",
        require_write=lambda: None,
    )

    async def api(method, path, **kwargs):
        if path == "/api/account/me":
            return {"github_installation_id": "7"}
        return caller.app

    async def require_role(project, minimum):
        rank = list(reversed(ProjectRole))
        if rank.index(caller.role) < rank.index(minimum):
            raise internal_api.ApiError(403, "Insufficient project role")

    caller.api = api
    caller.require_role = require_role
    monkeypatch.setattr(mcp_server, "Caller", lambda: caller)
    monkeypatch.setattr(
        mcp_server,
        "get_installation_token",
        mock.AsyncMock(return_value="tok"),
    )
    monkeypatch.setattr(
        mcp_server, "_gate", mock.AsyncMock(return_value=("go", None))
    )
    monkeypatch.setattr(mcp_server.audit, "record_agent_event", mock.Mock())
    monkeypatch.setattr(
        repo_ops,
        "commit_files",
        mock.AsyncMock(
            return_value=SimpleNamespace(
                sha="abc123def456", branch="main", pull_request_url=None
            )
        ),
    )
    monkeypatch.setattr(
        repo_ops, "list_files", mock.AsyncMock(return_value=["README.md"])
    )
    return caller


def _app(status: str, name: str = "shop", url: str = SHOP_URL) -> dict:
    return {
        "id": 5,
        "name": name,
        "project_id": "attacker",
        "status": status,
        "github_repo_url": url,
    }


@pytest.mark.parametrize("status", ["failed", "deleted"])
def test_mcp_refuses_to_list_files_of_an_app_that_is_not_running(
    mcp_caller, status
):
    mcp_caller.app = _app(
        status, "cnp-projects", "https://github.com/3-Istor/cnp-projects"
    )

    result = asyncio.run(mcp_server.list_app_files(5))

    assert result["ok"] is False
    repo_ops.list_files.assert_not_awaited()


def test_mcp_refuses_to_push_to_a_repository_owned_by_another_org(
    mcp_caller,
):
    mcp_caller.app = _app("running", url="https://github.com/evil/shop")

    result = asyncio.run(
        mcp_server.push_files(5, {"src/index.html": "<p>hi</p>"}, "feat: x")
    )

    assert result["ok"] is False
    repo_ops.commit_files.assert_not_awaited()


def _post_app(factory, name: str, app_config: dict | None = None):
    with factory() as db:
        db.add(Project(project_name="demo", owner_username="u"))
        db.commit()
    app = FastAPI()
    app.include_router(router.router, prefix="/api")

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    with (
        mock.patch.object(router, "is_cnp_admin", lambda token: True),
        mock.patch.object(router, "ensure_app_names_free", mock.AsyncMock()),
        mock.patch.object(router.terraform_orchestrator, "run_deployment"),
        mock.patch.object(state, "fresh_state", lambda cloud: "up"),
    ):
        return TestClient(app).post(
            "/api/deployments/",
            json={
                "name": name,
                "template_id": "k3s-gitops-app",
                "project_id": "demo",
                "app_config": app_config or {"app_type": "static"},
            },
        )


@pytest.mark.parametrize("name", ["cnp-projects", "CMP", "app-templates"])
def test_app_named_like_a_platform_repository_is_refused_at_creation(
    session_factory, name  # noqa: F811
):
    response = _post_app(session_factory, name)

    assert response.status_code == 409


def test_client_supplied_github_owner_is_replaced_by_the_platform_org(
    session_factory,  # noqa: F811
):
    response = _post_app(
        session_factory, "shop", {"app_type": "static", "github_owner": "evil"}
    )

    assert response.status_code == 202
    with session_factory() as db:
        config = json.loads(db.query(Deployment).one().app_config)
    assert config["github_owner"] == "3-Istor"


def test_backfill_gives_a_url_only_to_apps_whose_apply_succeeded(
    session_factory, monkeypatch  # noqa: F811
):
    with session_factory() as db:
        for name, status in [
            ("shop", DeploymentStatus.RUNNING),
            ("cnp-docs-clone", DeploymentStatus.FAILED),
            ("blog", DeploymentStatus.DELETED),
        ]:
            db.add(
                Deployment(
                    name=name, template_id="k3s-gitops-app", status=status
                )
            )
        db.commit()
    monkeypatch.setattr(
        terraform_orchestrator, "SessionLocal", session_factory
    )

    terraform_orchestrator.backfill_gitops_repo_urls()

    with session_factory() as db:
        urls = {d.name: d.github_repo_url for d in db.query(Deployment)}
    assert urls == {"shop": SHOP_URL, "cnp-docs-clone": None, "blog": None}


def test_rest_day2_helpers_refuse_a_failed_app_holding_a_platform_url():
    deployment = Deployment(
        name="cnp-projects",
        status=DeploymentStatus.FAILED,
        github_repo_url="https://github.com/3-Istor/cnp-projects",
    )

    with pytest.raises(HTTPException) as refused:
        router._extract_repo_full_name(deployment)

    assert refused.value.status_code == 409
