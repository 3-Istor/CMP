import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import TargetCloud
from app.routers import placement as router
from app.services import placement
from app.services.keycloak_service import get_current_user
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
)


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
        ("gatus-7cb854656d-5mx6t", {}, "monitor"),
        ("migrate-x", {"cnp.3istor.com/role": "migration"}, "job"),
        (
            "iot-api-869cf6f-xfggt",
            {"app.kubernetes.io/instance": "iot-api-backend"},
            "api",
        ),
        (
            "iot-api-869cf6f-xfggt",
            {"app.kubernetes.io/instance": "iot-api-backend-stg"},
            "api",
        ),
        (
            "iot-web-1",
            {"app.kubernetes.io/instance": "iot-web-frontend"},
            "web",
        ),
        ("shop-1", {"app.kubernetes.io/instance": "shop-app"}, "web"),
        ("offhours-guard-1", {}, "system"),
    ],
)
def test_pod_kind(name, labels, kind):
    assert placement.pod_kind(_pod(name, labels=labels)) == kind


@pytest.mark.parametrize(
    "name,generation",
    [("todo-move-todo-backend-db-r4", 4), ("todo-move-todo-backend-db", 0)],
)
def test_database_generation_is_the_restore_suffix(name, generation):
    cluster = {"metadata": {"name": name, "generation": 1}}
    assert placement.database(cluster)["generation"] == generation


def test_database_archiving_comes_from_the_condition():
    cluster = {
        "metadata": {"name": "d"},
        "status": {
            "conditions": [
                {
                    "type": "ContinuousArchiving",
                    "status": "True",
                    "lastTransitionTime": "t",
                }
            ]
        },
    }
    assert placement.database(cluster)["archiving"] is True
    assert placement.database({"metadata": {"name": "d"}})["archiving"] is None


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
            pod = _pod(
                "demo-web",
                labels={"app.kubernetes.io/instance": "demo-frontend"},
            )
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
        placement, "visible_app_counts", lambda db: {"demo": 1}
    )
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


def test_forbidden_nodes_do_not_hide_the_pods(client, monkeypatch):
    kube = FakeKube()

    def read(path, params=None, cloud=TargetCloud.ONPREM):
        if path == "/api/v1/nodes":
            raise KubeUnavailableError("forbidden", 403)
        return kube(path, params, cloud)

    monkeypatch.setattr(placement, "kube_list", read)
    body = client.get("/api/projects/demo/placement").json()
    onprem = next(c for c in body["clusters"] if c["cloud"] == "onprem")

    assert onprem["state"] == "up"
    assert onprem["nodes"][0]["name"] == "n1"
    assert onprem["nodes"][0]["instance_type"] is None


def test_platform_map_lists_projects_per_cluster(client):
    body = client.get("/api/placement").json()
    by_cloud = {c["cloud"]: c for c in body["clusters"]}

    assert by_cloud["gcp"]["projects"] == [
        {"name": "demo", "apps": 1, "moving_to": None, "moving_from": "aws"}
    ]
    assert by_cloud["onprem"]["projects"][0]["name"] == "home"
    assert by_cloud["onprem"]["memory_requested_pct"] == 25.0
    assert by_cloud["aws"]["state"] == "unreachable"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("6085816Ki", 6085816 * 1024),
        ("1Gi", 2**30),
        ("100M", 100e6),
        ("1500k", 1.5e6),
        ("104857600", 104857600),
        ("1e9", 1e9),
        ("500m", 0.5),
    ],
)
def test_quantity_bytes(value, expected):
    assert placement.quantity_bytes(value) == expected


def test_memory_pct_computes_on_gke_shaped_objects(monkeypatch):
    nodes = [
        {"status": {"allocatable": {"memory": "6085816Ki"}}},
        {"status": {"allocatable": {"memory": "6085816Ki"}}},
    ]
    pods = [
        {
            "spec": {
                "containers": [{"resources": {"requests": {"memory": "1Gi"}}}]
            }
        },
        {
            "spec": {
                "containers": [
                    {"resources": {"requests": {"memory": "100M"}}},
                    {"resources": {}},
                ]
            }
        },
        {  # a finished job holds nothing
            "status": {"phase": "Succeeded"},
            "spec": {
                "containers": [{"resources": {"requests": {"memory": "4Gi"}}}]
            },
        },
    ]
    monkeypatch.setattr(
        placement,
        "kube_list",
        lambda path, params=None, cloud=None: (
            nodes if "nodes" in path else pods
        ),
    )
    pct = placement._memory_requested_pct(TargetCloud.GCP)
    assert pct == round(100 * (2**30 + 100e6) / (2 * 6085816 * 1024), 1)


def test_forbidden_nodes_still_count_as_up(client, monkeypatch):
    kube = FakeKube()

    def read(path, params=None, cloud=TargetCloud.ONPREM):
        if path == "/api/v1/nodes":
            raise KubeUnavailableError("forbidden", 403)
        return kube(path, params, cloud)

    monkeypatch.setattr(placement, "kube_list", read)
    onprem = next(
        c
        for c in client.get("/api/placement").json()["clusters"]
        if c["cloud"] == "onprem"
    )
    assert onprem["state"] == "up"
    assert onprem["memory_requested_pct"] is None


def test_iot_on_prem_project(client, monkeypatch):
    """An on-prem project: pods on their nodes, both backup claims, db generation 0."""

    def pod(name, node, instance, ns):
        p = _pod(name, labels={"app.kubernetes.io/instance": instance})
        p["metadata"]["namespace"] = ns
        p["spec"]["nodeName"] = node
        return p

    pods = {
        "iot-optispace-backend": [
            pod(
                "optispace-be-1",
                "my-k3s-cluster-node",
                "iot-optispace-backend",
                "iot-optispace-backend",
            ),
        ],
        "iot-optispace-backend-stg": [
            pod(
                "optispace-be-stg-1",
                "k3s-worker-node",
                "iot-optispace-backend-stg",
                "iot-optispace-backend-stg",
            ),
        ],
        "iot-landing": [
            pod(
                "landing-1",
                "k3s-worker-node",
                "iot-landing-app",
                "iot-landing",
            ),
        ],
    }
    db = {
        "metadata": {"name": "iot-optispace-backend-db", "generation": 3},
        "status": {
            "conditions": [{"type": "ContinuousArchiving", "status": "True"}]
        },
    }
    claims = [
        {
            "metadata": {
                "name": "iot-optispace-backend-db-backup",
                "uid": "u1",
            },
            "spec": {"bucketName": "cnpg-iot-optispace-backend-db"},
        },
        {
            "metadata": {
                "name": "iot-optispace-backend-stg-db-backup",
                "uid": "u2",
            },
            "spec": {"bucketName": "cnpg-iot-optispace-backend-stg-db"},
        },
    ]

    def read(path, params=None, cloud=TargetCloud.ONPREM):
        if cloud != TargetCloud.ONPREM:
            raise ClusterNotConnectedError(cloud)
        if path == "/api/v1/nodes":
            raise KubeUnavailableError("forbidden", 403)
        if path == "/api/v1/namespaces":
            return [{"metadata": {"name": n}} for n in pods]
        if path.endswith("/pods"):
            return pods[path.split("/")[4]]
        if path.endswith("/clusters"):
            return [db] if "iot-optispace-backend/" in path else []
        if path.endswith("objectbucketclaims"):
            return claims if "/iot-backups/" in path else []
        return []

    monkeypatch.setattr(placement, "kube_list", read)
    monkeypatch.setattr(placement, "fresh_state", lambda cloud: "up")
    body = client.get("/api/projects/iot/placement").json()
    onprem = next(c for c in body["clusters"] if c["cloud"] == "onprem")

    assert body["current_cloud"] == "onprem"
    assert onprem["state"] == "up"
    on_node = {
        n["name"]: [p["name"] for p in n["pods"]] for n in onprem["nodes"]
    }
    assert on_node == {
        "my-k3s-cluster-node": ["optispace-be-1"],
        "k3s-worker-node": ["optispace-be-stg-1", "landing-1"],
    }
    assert body["database"]["name"] == "iot-optispace-backend-db"
    assert body["database"]["generation"] == 0
    assert [(b["claim"], b["bucket"]) for b in body["backups"]] == [
        ("iot-optispace-backend-db-backup", "cnpg-iot-optispace-backend-db"),
        (
            "iot-optispace-backend-stg-db-backup",
            "cnpg-iot-optispace-backend-stg-db",
        ),
    ]


def test_app_count_is_the_cmp_apps_not_the_registry_entries():
    # Arrange: the registry also lists an agent; the CMP holds one app
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        for id_, status in (
            (1, DeploymentStatus.RUNNING),
            (2, DeploymentStatus.DELETED),
        ):
            db.add(
                Deployment(
                    id=id_,
                    name=f"app{id_}",
                    template_id="k3s-gitops-app",
                    project_id="demo",
                    provider_type=ProviderType.KUBERNETES,
                    status=status,
                )
            )
        db.commit()

        # Act
        counts = placement.visible_app_counts(db)

    # Assert
    assert counts == {"demo": 1}
