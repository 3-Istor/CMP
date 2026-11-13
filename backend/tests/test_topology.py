import copy
from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.roles import ProjectRole, at_least
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.imports import ImportRun, ImportService, ImportSource
from app.routers import topology as topology_router
from app.services import authz, topology
from app.services.imports import app_links, service

SHA = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "a" * 64

RECORD = {
    "metadata": {"name": "shop"},
    "spec": {
        "apps": [
            {
                "name": "web",
                "type": "static",
                "repoURL": "https://github.com/3-Istor/web",
                "hostnames": {"prod": "web-shop.3istor.com"},
            },
            {
                "name": "api",
                "type": "service",
                "public": True,
                "hostnames": {"prod": "api-shop.3istor.com"},
                "source": {"repo": "x", "path": "api", "sha": SHA},
                "dependsOn": [
                    {"app": "mongodb", "port": 27017},
                    {"app": "cache", "port": 6379, "alias": "redis"},
                ],
            },
            {
                "name": "worker",
                "type": "service",
                "source": {"repo": "x", "path": "worker", "sha": SHA},
            },
            {"name": "mongodb", "type": "backing"},
            {"name": "cache", "type": "backing"},
        ]
    },
}


class World:
    def __init__(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, expire_on_commit=False)
        self.roles = {"shop": ProjectRole.ADMIN}
        self.record = copy.deepcopy(RECORD)
        self.commits: list[str] = []
        self.flows: list[dict] = []
        self.metric_present = True
        self.queries: list[str] = []
        self.applications: list[dict] = []
        self.schedules: dict[str, list[dict]] = {}

    def db(self):
        return self.Session()


def _role_override(world, minimum):
    def dep(project_name: str):
        role = world.roles.get(project_name)
        if role is None or not at_least(role, minimum):
            raise HTTPException(403, "denied")
        return authz.ProjectContext(
            project_name=project_name,
            user_id="uid-alice",
            username="alice",
            role=role,
            token={"preferred_username": "alice"},
        )

    return dep


def _import_rows(world):
    with world.db() as db:
        source = ImportSource(
            project_name="shop",
            repo_full_name="3-Istor/shop",
            repo_id=1,
            installation_id=7,
            branch="main",
            created_by="alice",
        )
        db.add(source)
        for name, kind, ports, extra in (
            ("api", "service", [8080], {}),
            ("worker", "service", [9000], {}),
            ("mongodb", "backing", [27017], {"backing_kind": "mongodb"}),
            ("cache", "backing", [6379], {"backing_kind": "redis"}),
        ):
            db.add(
                ImportService(
                    source=source,
                    app_name=name,
                    kind=kind,
                    path=name,
                    status="running",
                    last_digest=DIGEST if kind == "service" else None,
                    spec={
                        "name": name,
                        "kind": kind,
                        "ports": [{"name": "p", "port": p} for p in ports],
                        "depends_on": [],
                        **extra,
                    },
                )
            )
        db.add(
            Deployment(
                name="web",
                template_id="static-site",
                project_id="shop",
                provider_type=ProviderType.KUBERNETES,
                status=DeploymentStatus.RUNNING,
            )
        )
        db.commit()
        return source


@pytest.fixture
def world(monkeypatch):
    world = World()
    _import_rows(world)

    async def read_record(project):
        return (
            (copy.deepcopy(world.record), "sha") if project == "shop" else None
        )

    async def commit(project, change, message):
        record = copy.deepcopy(world.record)
        await change(record)
        world.record = record
        world.commits.append(message)
        return "c0ffee"

    def kube_list(path, params=None, cloud=None):
        if "applications" in path:
            return world.applications
        namespace = path.split("/namespaces/")[1].split("/")[0]
        return world.schedules.get(namespace, [])

    async def vm_query(client, query, cloud):
        world.queries.append(query)
        if query.startswith("count("):
            return [{"value": [0, "1"]}] if world.metric_present else []
        return world.flows

    monkeypatch.setattr(topology, "read_record", read_record)
    monkeypatch.setattr(topology, "kube_list", kube_list)
    monkeypatch.setattr(topology, "_vm_query", vm_query)
    monkeypatch.setattr(app_links, "commit_record_change", commit)
    return world


@pytest.fixture
def client(world):
    app = FastAPI()
    app.include_router(topology_router.router, prefix="/api")

    def get_session():
        db = world.db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_session
    for annotation, minimum in (
        (authz.RequireAdmin, ProjectRole.ADMIN),
        (authz.RequireMember, ProjectRole.MEMBER),
    ):
        app.dependency_overrides[annotation.__metadata__[0].dependency] = (
            _role_override(world, minimum)
        )
    return TestClient(app)


def _flow(source, target, verdict, value):
    return {
        "metric": {
            "source_namespace": f"shop-{source}",
            "destination_namespace": f"shop-{target}",
            "source_workload": source,
            "destination_workload": target,
            "verdict": verdict,
        },
        "value": [0, str(value)],
    }


def _edges(response):
    return {(e["from"], e["to"]): e for e in response.json()["edges"]}


# ── Edge states and PromQL ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "declared, forwarded, dropped, state",
    [
        (True, 1.0, 0.0, "declared_observed"),
        (True, 0.0, 0.0, "declared_only"),
        (False, 2.0, 0.0, "observed_only"),
        (False, 0.0, 3.0, "denied"),
        (True, 0.0, 3.0, "denied"),
        (True, 1.0, 3.0, "declared_observed"),
    ],
)
def test_edge_state_follows_declaration_and_verdicts(
    declared, forwarded, dropped, state
):
    assert topology.edge_state(declared, forwarded, dropped) == state


def test_flow_query_matches_the_project_namespaces_exactly():
    query = topology.flow_query(["shop-api", "shop-db"], "rate", "5m")

    assert 'source_namespace=~"shop-api|shop-db"' in query


def test_flow_query_refuses_a_namespace_that_is_not_a_dns_label():
    with pytest.raises(ValueError):
        topology.flow_query(['shop-api"} or vector(1) #'], "rate", "5m")


# ── GET topology ────────────────────────────────────────────────────────────


def test_member_reads_nodes_from_registry_deployments_and_imports(
    client, world
):
    world.roles["shop"] = ProjectRole.MEMBER

    response = client.get("/api/projects/shop/topology")

    nodes = {n["id"]: n for n in response.json()["nodes"]}
    assert {
        name: (n["kind"], n["imported"], n["public"])
        for name, n in nodes.items()
    } == {
        "web": ("static", False, True),
        "api": ("service", True, True),
        "worker": ("service", True, False),
        "mongodb": ("backing", True, False),
        "cache": ("backing", True, False),
    }


def test_guest_cannot_read_the_topology(client, world):
    world.roles["shop"] = ProjectRole.GUEST

    response = client.get("/api/projects/shop/topology")

    assert response.status_code == 403


def test_node_takes_health_and_sync_from_argo_and_sleep_from_offhours(
    client, world
):
    world.applications = [
        {
            "metadata": {"name": "shop-api-app"},
            "status": {
                "health": {"status": "Degraded"},
                "sync": {"status": "OutOfSync"},
            },
        }
    ]
    world.schedules["shop-worker"] = [{"status": {"currentState": "SLEEPING"}}]

    nodes = {
        n["id"]: n
        for n in client.get("/api/projects/shop/topology").json()["nodes"]
    }

    assert (
        nodes["api"]["health"],
        nodes["api"]["sync"],
        nodes["worker"]["asleep"],
        nodes["api"]["asleep"],
    ) == ("degraded", "outofsync", True, False)


def test_declared_link_without_traffic_is_declared_only(client, world):
    response = client.get("/api/projects/shop/topology")

    edge = _edges(response)[("api", "cache")]
    assert (edge["state"], edge["alias"], edge["port"], edge["observed"]) == (
        "declared_only",
        "redis",
        6379,
        None,
    )


def test_observed_flows_mark_edges_observed_undeclared_and_denied(
    client, world
):
    world.flows = [
        _flow("api", "mongodb", "FORWARDED", 2.5),
        _flow("worker", "api", "FORWARDED", 1),
        _flow("worker", "mongodb", "DROPPED", 4),
        _flow("api", "api", "FORWARDED", 9),
    ]

    edges = _edges(client.get("/api/projects/shop/topology"))

    assert {pair: e["state"] for pair, e in edges.items()} == {
        ("api", "mongodb"): "declared_observed",
        ("api", "cache"): "declared_only",
        ("worker", "api"): "observed_only",
        ("worker", "mongodb"): "denied",
    }


def test_flows_of_another_project_are_never_queried(client, world):
    client.get("/api/projects/shop/topology")

    assert all("shop-api|" in q for q in world.queries if "sum by" in q)
    assert not any("other-" in q for q in world.queries)


def test_without_hubble_metrics_the_map_says_observed_is_unavailable(
    client, world
):
    world.metric_present = False

    body = client.get("/api/projects/shop/topology").json()

    assert body["observed_available"] is False


# ── Links ───────────────────────────────────────────────────────────────────


def _link(client, **body):
    return client.post(
        "/api/projects/shop/topology/links",
        json={"from": "worker", "to": "api", "port": 8080, **body},
    )


def test_admin_links_two_imported_apps_in_the_registry(client, world):
    response = _link(client, alias="backend")

    worker = next(
        a for a in world.record["spec"]["apps"] if a["name"] == "worker"
    )
    assert (response.status_code, worker["dependsOn"]) == (
        201,
        [{"app": "api", "port": 8080, "alias": "backend"}],
    )


def test_link_is_kept_on_the_import_row_for_the_next_build(client, world):
    _link(client)

    with world.db() as db:
        row = db.query(ImportService).filter_by(app_name="worker").one()
        assert row.spec["depends_on"] == [
            {"app": "api", "port": 8080, "alias": "api", "manual": True}
        ]


def test_member_cannot_link_apps(client, world):
    world.roles["shop"] = ProjectRole.MEMBER

    response = _link(client)

    assert (response.status_code, world.commits) == (403, [])


@pytest.mark.parametrize(
    "body, status",
    [
        ({"to": "web"}, 422),
        ({"from": "web"}, 422),
        ({"to": "nope"}, 422),
        ({"port": 9999}, 422),
        ({"alias": "vault"}, 422),
        ({"alias": "worker-canary"}, 422),
        ({"alias": "9lives"}, 422),
        ({"to": "worker"}, 422),
    ],
)
def test_link_the_registry_would_refuse_is_refused(
    client, world, body, status
):
    response = _link(client, **body)

    assert (response.status_code, world.commits) == (status, [])


def test_alias_already_resolved_by_the_caller_is_a_conflict(client, world):
    response = client.post(
        "/api/projects/shop/topology/links",
        json={"from": "api", "to": "worker", "port": 9000, "alias": "redis"},
    )

    assert response.status_code == 409


def test_link_waits_for_a_running_import_of_the_caller(client, world):
    with world.db() as db:
        source = db.query(ImportSource).one()
        db.add(
            ImportRun(
                source_id=source.id,
                kind="build",
                sha=SHA,
                status="running",
                requested_by="alice",
                expires_at=service._now() + timedelta(hours=1),
            )
        )
        db.commit()

    response = _link(client)

    assert response.status_code == 409


def test_admin_unlinks_one_alias(client, world):
    response = client.delete(
        "/api/projects/shop/topology/links",
        params={"from": "api", "to": "cache", "alias": "redis"},
    )

    api = next(a for a in world.record["spec"]["apps"] if a["name"] == "api")
    assert (response.status_code, api["dependsOn"]) == (
        200,
        [{"app": "mongodb", "port": 27017}],
    )


def test_unlinking_a_link_that_does_not_exist_is_not_found(client, world):
    response = client.delete(
        "/api/projects/shop/topology/links",
        params={"from": "worker", "to": "api"},
    )

    assert (response.status_code, world.commits) == (404, [])
