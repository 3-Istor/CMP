import json
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.deployment import Deployment
from app.models.project import Project, TargetCloud
from app.routers import deployments as router
from app.services.clusters import state
from app.services.keycloak_service import get_current_user
from tests.test_app_inputs import TEMPLATE
from tests.test_app_security_data import session_factory  # noqa: F401


@pytest.mark.parametrize(
    "cloud,expected",
    [
        (TargetCloud.AWS, "aws"),
        (TargetCloud.ONPREM, "onprem"),
    ],
)
def test_app_config_gets_the_project_cloud(
    session_factory, cloud, expected  # noqa: F811
):
    with session_factory() as db:
        db.add(
            Project(
                project_name="demo", owner_username="u", target_cloud=cloud
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
    config = {"template_repo_name": "template-html-css"}

    with (
        mock.patch.object(router, "is_cnp_admin", lambda token: True),
        mock.patch.object(router, "get_template_by_id", lambda _id: TEMPLATE),
        mock.patch.object(router, "ensure_app_names_free", mock.AsyncMock()),
        mock.patch.object(router.terraform_orchestrator, "run_deployment"),
        mock.patch.object(state, "fresh_state", lambda cloud: "up"),
    ):
        response = TestClient(app).post(
            "/api/deployments/",
            json={
                "name": "shop",
                "template_id": "k3s-gitops-app",
                "project_id": "demo",
                "app_config": config,
            },
        )

    assert response.status_code == 202
    with session_factory() as db:
        row = db.query(Deployment).one()
        assert json.loads(row.app_config)["target_cloud"] == expected


def _post_deploy(session_factory, project_cloud, cluster):  # noqa: F811
    with session_factory() as db:
        db.add(
            Project(
                project_name="demo",
                owner_username="u",
                target_cloud=project_cloud,
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
        mock.patch.object(state, "fresh_state", lambda cloud: cluster),
    ):
        return TestClient(app).post(
            "/api/deployments/",
            json={
                "name": "shop",
                "template_id": "k3s-gitops-app",
                "project_id": "demo",
                "app_config": {"template_repo_name": "template-html-css"},
            },
        )


def test_deploy_refused_when_cluster_down(session_factory):  # noqa: F811
    r = _post_deploy(session_factory, TargetCloud.AWS, "down")
    assert r.status_code == 409
    assert "AWS cluster is down" in r.json()["detail"]


def test_deploy_allowed_when_cluster_up(session_factory):  # noqa: F811
    assert (
        _post_deploy(session_factory, TargetCloud.AWS, "up").status_code == 202
    )


def test_onprem_ignores_cluster_state(session_factory):  # noqa: F811
    r = _post_deploy(session_factory, TargetCloud.ONPREM, "down")
    assert r.status_code == 202


def test_mcp_release_guard_uses_the_project_cloud(
    session_factory,
):  # noqa: F811
    import asyncio

    from app import mcp_server
    from app.services.agents import internal_api

    with session_factory() as db:
        db.add(
            Project(
                project_name="demo",
                owner_username="u",
                target_cloud=TargetCloud.AWS,
            )
        )
        db.commit()

    caller = object.__new__(mcp_server.Caller)
    with (
        mock.patch.object(mcp_server, "SessionLocal", session_factory),
        mock.patch.object(state, "fresh_state", lambda cloud: "down"),
    ):
        with pytest.raises(internal_api.ApiError) as exc:
            asyncio.run(caller.require_cluster_up("demo"))
    assert exc.value.status_code == 409


def test_template_app_cannot_take_a_name_held_by_an_import(
    session_factory,  # noqa: F811
):
    from app.models.imports import ImportService, ImportSource

    with session_factory() as db:
        source = ImportSource(
            project_name="demo",
            repo_full_name="acme/shop",
            repo_id=1,
            installation_id=2,
            branch="main",
            created_by="u",
        )
        db.add(source)
        db.flush()
        db.add(
            ImportService(source_id=source.id, app_name="shop", kind="service")
        )
        db.commit()

    response = _post_deploy(session_factory, TargetCloud.ONPREM, "up")

    assert response.status_code == 409
    assert "repository import" in response.json()["detail"]
    with session_factory() as db:
        assert db.query(Deployment).count() == 0
