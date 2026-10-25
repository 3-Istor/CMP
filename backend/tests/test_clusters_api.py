import asyncio
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


@pytest.fixture(autouse=True)
def reset_refresh():
    infra._last_refresh, infra._refresh_running = None, False
    yield
    infra._last_refresh, infra._refresh_running = None, False


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


def test_affected_apps_lists_apps_without_stopping(session_factory, run_op):  # noqa: F811
    set_state(session_factory, ClusterState.UP)
    with session_factory() as db:
        db.add(Project(project_name="demo", owner_username="u", target_cloud=TargetCloud.AWS))
        db.add(Deployment(name="shop", project_id="demo", template_id="t",
                          status=DeploymentStatus.PENDING))
        db.commit()
    r = make_client(session_factory, ADMIN).get("/api/clusters/aws/affected-apps")
    assert r.status_code == 200
    assert [(a["project"], a["app"]) for a in r.json()["affected_apps"]] == [("demo", "shop")]
    assert make_client(session_factory, USER).get("/api/clusters/aws/affected-apps").status_code == 403
    assert read_state(session_factory) == ClusterState.UP
    run_op.assert_not_called()


def test_down_starts(session_factory, run_op):  # noqa: F811
    set_state(session_factory, ClusterState.UP)
    r = make_client(session_factory, ADMIN).post("/api/clusters/aws/down")
    assert r.status_code == 202
    assert r.json()["state"] == "stopping"
    run_op.assert_called_once_with(TargetCloud.AWS, "down")


def test_down_when_already_down_conflicts(session_factory, run_op):  # noqa: F811
    r = make_client(session_factory, ADMIN).post("/api/clusters/gcp/down")
    assert r.status_code == 409
    run_op.assert_not_called()


@pytest.mark.parametrize("token,expected", [(ADMIN, True), (USER, False)])
def test_health_exposes_clusters_and_admin_flag(session_factory, token, expected):  # noqa: F811
    set_state(session_factory, ClusterState.UP)
    with (
        mock.patch.object(infra, "_schedule_refresh"),
        mock.patch.object(infra, "wireguard_cached", lambda cloud: True),
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
        mock.patch.object(infra, "_schedule_refresh", mock.Mock(side_effect=RuntimeError)),
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


def test_health_does_not_wait_for_the_refresh(session_factory):  # noqa: F811
    async def slow_refresh():
        await asyncio.Event().wait()  # never set

    async def call():
        with session_factory() as db:
            health = await asyncio.wait_for(
                infra.get_global_infrastructure_health(ADMIN, db), timeout=2
            )
        for t in list(infra._refresh_tasks):
            t.cancel()
        return health

    with (
        mock.patch.object(infra, "_refresh_clusters", slow_refresh),
        mock.patch.object(infra.monitoring_service, "get_global_health",
                          mock.AsyncMock(return_value=infra.GlobalHealthResponse(openstack_hypervisors=[]))),
    ):
        health = asyncio.run(call())
    assert [c.cloud for c in health.clusters] == ["aws", "gcp"]


def test_concurrent_stale_health_calls_schedule_one_refresh():
    started = mock.Mock()

    async def slow_refresh():
        started()
        await asyncio.Event().wait()

    async def call():
        infra._schedule_refresh()
        infra._schedule_refresh()
        await asyncio.sleep(0)
        for t in list(infra._refresh_tasks):
            t.cancel()

    with mock.patch.object(infra, "_refresh_clusters", slow_refresh):
        asyncio.run(call())
    started.assert_called_once()


def test_global_health_caches_hypervisor_listing(monkeypatch):
    fetch = mock.AsyncMock(return_value=[])
    monkeypatch.setattr(infra.monitoring_service, "_get_openstack_hypervisors", fetch)
    monkeypatch.setattr(infra.monitoring_service, "_hypervisors_cache", None)

    async def twice():
        await infra.monitoring_service.get_global_health()
        await infra.monitoring_service.get_global_health()

    asyncio.run(twice())
    fetch.assert_awaited_once()
