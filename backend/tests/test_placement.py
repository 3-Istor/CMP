import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.project import TargetCloud
from app.routers import placement as router
from app.services import placement
from app.services.keycloak_service import get_current_user
from app.services.kube_client import ClusterNotConnectedError


def _pod(name="p", phase="Running", ready=True, deleting=False, labels=None):
    meta = {"name": name, "namespace": "demo-api", "labels": labels or {}}
    if deleting:
        meta["deletionTimestamp"] = "2026-11-22T10:00:00Z"
    return {
        "metadata": meta,
        "spec": {"nodeName": "n1"},
        "status": {
            "phase": phase,
            "containerStatuses": [{"ready": ready}],
        },
    }


def test_pod_status_terminating_wins():
    assert placement.pod_status(_pod(deleting=True)) == "Terminating"


def test_pod_status_running_not_ready_is_starting():
    assert placement.pod_status(_pod(ready=False)) == "Starting"


def test_pod_status_phases():
    assert (
        placement.pod_status(_pod(phase="Pending", ready=False)) == "Pending"
    )
    assert placement.pod_status(_pod(phase="Failed")) == "Failed"
    assert placement.pod_status(_pod(phase="Succeeded")) == "Succeeded"
    assert placement.pod_status(_pod()) == "Running"


@pytest.mark.parametrize(
    "name,labels,kind",
    [
        ("demo-db-1", {"cnpg.io/cluster": "demo-db"}, "db"),
        ("demo-tunnel-abc", {}, "tunnel"),
        ("demo-gatus-abc", {}, "monitor"),
        ("demo-api", {"app.kubernetes.io/component": "backend"}, "api"),
        ("demo-web", {}, "web"),
    ],
)
def test_pod_kind(name, labels, kind):
    assert placement.pod_kind(_pod(name, labels=labels)) == kind


class FakeKube:
    """Per-cloud canned answers; a cloud in `down` is not connected."""

    def __init__(self, down=()):
        self.down = set(down)

    def __call__(self, path, params=None, cloud=TargetCloud.ONPREM):
        if cloud in self.down:
            raise ClusterNotConnectedError(cloud)
        if path == "/api/v1/nodes":
            return [
                {
                    "metadata": {
                        "name": "n1",
                        "labels": {
                            "node.kubernetes.io/instance-type": "m5.large"
                        },
                    },
                    "status": {"allocatable": {"memory": "8Gi"}},
                }
            ]
        if path == "/api/v1/namespaces":
            return [{"metadata": {"name": "demo-api"}}]
        if path.endswith("/pods"):
            pod = _pod("demo-web")
            pod["spec"]["containers"] = [
                {"resources": {"requests": {"memory": "2Gi"}}}
            ]
            return [pod]
        return []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        placement, "kube_list", FakeKube(down={TargetCloud.AWS})
    )
    monkeypatch.setattr(placement, "fresh_state", lambda cloud: "up")

    async def records():
        return [
            {
                "metadata": {"name": "demo"},
                "spec": {"targetCloud": "gcp", "apps": [{"name": "api"}]},
                "status": {
                    "move": {"id": 1, "source": "aws", "target": "gcp"}
                },
            },
            {"metadata": {"name": "home"}, "spec": {"targetCloud": "onprem"}},
        ]

    monkeypatch.setattr(placement.project_registry, "list_records", records)
    monkeypatch.setattr(
        router, "project_cloud", lambda db, name: TargetCloud.ONPREM
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    app.state.token = {"sub": "u", "groups": ["/infra"]}
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: app.state.token
    c = TestClient(app)
    c.app_ = app
    return c


def test_placement_requires_platform_admin(client):
    client.app_.state.token = {"sub": "u", "groups": ["/demo-admin"]}
    assert client.get("/api/projects/demo/placement").status_code == 403
    assert client.get("/api/placement").status_code == 403


def test_unreachable_cluster_is_marked(client):
    body = client.get("/api/projects/demo/placement").json()
    by_cloud = {c["cloud"]: c for c in body["clusters"]}

    assert by_cloud["aws"]["state"] == "unreachable"
    assert by_cloud["aws"]["nodes"] == []
    assert by_cloud["onprem"]["state"] == "up"
    node = by_cloud["onprem"]["nodes"][0]
    assert node["instance_type"] == "m5.large"
    assert node["pods"][0]["kind"] == "web"


def test_platform_map_lists_projects_per_cluster(client):
    body = client.get("/api/placement").json()
    by_cloud = {c["cloud"]: c for c in body["clusters"]}

    assert by_cloud["gcp"]["projects"] == [
        {"name": "demo", "apps": 1, "moving_to": None, "moving_from": "aws"}
    ]
    assert by_cloud["onprem"]["projects"][0]["name"] == "home"
    assert by_cloud["onprem"]["memory_requested_pct"] == 25.0
    assert by_cloud["aws"]["state"] == "unreachable"
