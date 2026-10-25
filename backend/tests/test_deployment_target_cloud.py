import json
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.cluster import Cluster, ClusterState
from app.models.deployment import Deployment
from app.models.project import Project, TargetCloud
from app.routers import deployments as router
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import session_factory  # noqa: F401


@pytest.mark.parametrize(
    "cloud,sent,expected",
    [
        (TargetCloud.AWS, None, "aws"),
        (TargetCloud.ONPREM, None, "onprem"),
        (TargetCloud.AWS, "gcp", "aws"),  # client value is overridden
    ],
)
def test_app_config_gets_the_project_cloud(
    session_factory, cloud, sent, expected  # noqa: F811
):
    with session_factory() as db:
        db.add(Project(project_name="demo", owner_username="u", target_cloud=cloud))
        db.add(Cluster(cloud=TargetCloud.AWS, state=ClusterState.UP))
        db.commit()

    app = FastAPI()
    app.include_router(router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    config = {"app_type": "static"}
    if sent:
        config["target_cloud"] = sent

    with (
        mock.patch.object(router, "is_cnp_admin", lambda token: True),
        mock.patch.object(router, "ensure_app_names_free", mock.AsyncMock()),
        mock.patch.object(router.terraform_orchestrator, "run_deployment"),
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


def _post_deploy(session_factory, project_cloud, cluster_state):  # noqa: F811
    with session_factory() as db:
        db.add(Project(project_name="demo", owner_username="u", target_cloud=project_cloud))
        db.add(Cluster(cloud=TargetCloud.AWS, state=cluster_state))
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
        mock.patch.object(router, "ensure_app_names_free", mock.AsyncMock()),
        mock.patch.object(router.terraform_orchestrator, "run_deployment"),
    ):
        return TestClient(app).post(
            "/api/deployments/",
            json={
                "name": "shop",
                "template_id": "k3s-gitops-app",
                "project_id": "demo",
                "app_config": {"app_type": "static"},
            },
        )


def test_deploy_refused_when_cluster_down(session_factory):  # noqa: F811
    r = _post_deploy(session_factory, TargetCloud.AWS, ClusterState.DOWN)
    assert r.status_code == 409
    assert "AWS cluster is down" in r.json()["detail"]


def test_deploy_allowed_when_cluster_up(session_factory):  # noqa: F811
    assert _post_deploy(session_factory, TargetCloud.AWS, ClusterState.UP).status_code == 202


def test_onprem_ignores_cluster_state(session_factory):  # noqa: F811
    r = _post_deploy(session_factory, TargetCloud.ONPREM, ClusterState.DOWN)
    assert r.status_code == 202


def test_mcp_release_guard_uses_the_project_cloud(session_factory):  # noqa: F811
    import asyncio

    from app import mcp_server
    from app.services.agents import internal_api

    with session_factory() as db:
        db.add(Project(project_name="demo", owner_username="u", target_cloud=TargetCloud.AWS))
        db.add(Cluster(cloud=TargetCloud.AWS, state=ClusterState.DOWN))
        db.commit()

    caller = object.__new__(mcp_server.Caller)
    with mock.patch.object(mcp_server, "SessionLocal", session_factory):
        with pytest.raises(internal_api.ApiError) as exc:
            asyncio.run(caller.require_cluster_up("demo"))
    assert exc.value.status_code == 409
