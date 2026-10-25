from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.cluster import Cluster, ClusterState
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.routers import clusters as router
from app.routers import infra
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import session_factory  # noqa: F401

ADMIN = {"sub": "a", "preferred_username": "a", "realm_access": {"roles": ["cnp-admin"]}}
USER = {"sub": "u", "preferred_username": "u"}


def make_client(session_factory, token):  # noqa: F811
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    app.include_router(infra.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: token
    return TestClient(app)


def set_state(session_factory, state, cloud=TargetCloud.AWS):  # noqa: F811
    with session_factory() as db:
        db.add(Cluster(cloud=cloud, state=state))
        db.commit()


def read_state(session_factory):  # noqa: F811
    with session_factory() as db:
        return db.get(Cluster, TargetCloud.AWS).state


@pytest.fixture
def run_op():
    with mock.patch.object(router, "run_operation", mock.AsyncMock()) as m:
        yield m


def test_up_forbidden_for_non_admin(session_factory, run_op):  # noqa: F811
    r = make_client(session_factory, USER).post("/api/clusters/aws/up")
    assert r.status_code == 403
    run_op.assert_not_called()


def test_unknown_cloud_is_404(session_factory, run_op):  # noqa: F811
    c = make_client(session_factory, ADMIN)
    assert c.post("/api/clusters/onprem/up").status_code == 404
    assert c.post("/api/clusters/nope/up").status_code == 404


def test_up_starts_operation(session_factory, run_op):  # noqa: F811
    r = make_client(session_factory, ADMIN).post("/api/clusters/aws/up")
    assert r.status_code == 202
    assert r.json()["state"] == "starting"
    assert r.json()["started_by"] == "a"
    run_op.assert_called_once_with(TargetCloud.AWS, "up")


def test_double_up_conflicts(session_factory, run_op):  # noqa: F811
    c = make_client(session_factory, ADMIN)
    assert c.post("/api/clusters/aws/up").status_code == 202
    assert c.post("/api/clusters/aws/up").status_code == 409
    run_op.assert_called_once()


def test_down_without_confirm_lists_apps(session_factory, run_op):  # noqa: F811
    set_state(session_factory, ClusterState.UP)
    with session_factory() as db:
        db.add(Project(project_name="demo", owner_username="u", target_cloud=TargetCloud.AWS))
        db.add(Deployment(name="shop", project_id="demo", template_id="t",
                          status=DeploymentStatus.PENDING))
        db.commit()
    c = make_client(session_factory, ADMIN)
    r = c.post("/api/clusters/aws/down")
    assert r.status_code == 200
    assert [(a["project"], a["app"]) for a in r.json()["affected_apps"]] == [("demo", "shop")]
    assert c.post("/api/clusters/aws/down", json={"confirm": False}).status_code == 200
    assert read_state(session_factory) == ClusterState.UP
    run_op.assert_not_called()


def test_down_confirmed_starts(session_factory, run_op):  # noqa: F811
    set_state(session_factory, ClusterState.UP)
    r = make_client(session_factory, ADMIN).post("/api/clusters/aws/down", json={"confirm": True})
    assert r.status_code == 202
    assert r.json()["state"] == "stopping"
    run_op.assert_called_once_with(TargetCloud.AWS, "down")


def test_down_when_already_down_conflicts(session_factory, run_op):  # noqa: F811
    r = make_client(session_factory, ADMIN).post("/api/clusters/gcp/down", json={"confirm": True})
    assert r.status_code == 409
    run_op.assert_not_called()


@pytest.mark.parametrize("token,expected", [(ADMIN, True), (USER, False)])
def test_health_exposes_clusters_and_admin_flag(session_factory, token, expected):  # noqa: F811
    set_state(session_factory, ClusterState.UP)
    with (
        mock.patch.object(infra, "reconcile", mock.AsyncMock()),
        mock.patch.object(infra, "wireguard_running", mock.AsyncMock(return_value=True)),
        mock.patch.object(infra.monitoring_service, "get_global_health",
                          mock.AsyncMock(return_value=infra.GlobalHealthResponse(openstack_hypervisors=[]))),
    ):
        r = make_client(session_factory, token).get("/api/infra/health")
    body = r.json()
    assert body["can_operate_clusters"] is expected
    by_cloud = {c["cloud"]: c for c in body["clusters"]}
    assert by_cloud["aws"]["state"] == "up" and by_cloud["aws"]["wireguard_running"] is True
    assert by_cloud["gcp"]["state"] == "down" and by_cloud["gcp"]["wireguard_running"] is None


def test_health_survives_cluster_failure(session_factory):  # noqa: F811
    with (
        mock.patch.object(infra, "reconcile", mock.AsyncMock(side_effect=RuntimeError)),
        mock.patch.object(infra.monitoring_service, "get_global_health",
                          mock.AsyncMock(return_value=infra.GlobalHealthResponse(openstack_hypervisors=[]))),
    ):
        r = make_client(session_factory, ADMIN).get("/api/infra/health")
    assert r.status_code == 200 and r.json()["clusters"] == []


def test_logs_stream_drains_file(session_factory, tmp_path):  # noqa: F811
    log = tmp_path / "op.log"
    log.write_text("one\ntwo\n")
    with session_factory() as db:
        db.add(Cluster(cloud=TargetCloud.AWS, state=ClusterState.UP, log_path=str(log)))
        db.commit()
    with mock.patch.object(router, "_busy", lambda cloud: False):
        r = make_client(session_factory, ADMIN).get("/api/clusters/aws/logs/stream")
        assert r.text == "data: one\n\ndata: two\n\n"
        assert make_client(session_factory, USER).get("/api/clusters/aws/logs/stream").status_code == 403
