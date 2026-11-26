"""Read access per identity, as the documented access matrix states it."""

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models import imports as _import_models  # noqa: F401  (tables)
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, TargetCloud
from app.routers import alerting, app_observability, app_overview
from app.models.security import SecurityException
from app.routers import deployments, environments, finops, imports, projects
from app.routers import releases, security
from app.services.security.model import Tier
from app.services import app_overview as overview
from app.services import keycloak_service
from app.services.environments import runs
from app.services.imports import service as import_service
from app.services.observability import (
    app_alerts,
    app_costs,
    app_logs,
    app_traces,
    apps_health,
    signals,
    stores,
    summary,
)

IDS = "infra auditor finops owner admin member guest none".split()
# (groups, project role, owner of "shop") per identity, in IDS order.
IDENTITIES = [
    (["infra"], None, False),
    (["security-auditor"], None, False),
    (["finops"], None, False),
    ([], None, True),
    ([], "admin", False),
    ([], "member", False),
    ([], "guest", False),
    ([], None, False),
]

OK, NO, NF = 200, 403, 404  # NF: allowed, unknown item
APP, PRJ = "/deployments/1", "/projects/shop"
ROWS = {  # under /api
    f"{PRJ}/apps": (OK, OK, OK, OK, OK, OK, OK, NO),
    APP: (OK, OK, OK, OK, OK, OK, OK, NO),
    f"{APP}/config": (OK, OK, NO, OK, OK, OK, NO, NO),
    f"{APP}/metrics": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{APP}/observability/slo": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{APP}/observability": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{APP}/observability/signals": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{APP}/observability/deploys": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{APP}/observability/dora": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{APP}/summary": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{PRJ}/apps-health": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{APP}/outputs": (OK, OK, OK, OK, OK, OK, OK, NO),
    f"{PRJ}/deletion-protection": (OK, OK, NO, OK, OK, OK, NO, NO),
    f"{PRJ}/customer-access": (OK, OK, NO, OK, OK, OK, NO, NO),
    f"{APP}/observability/traces": (OK, NO, NO, OK, OK, OK, NO, NO),
    f"{APP}/observability/logs": (OK, NO, NO, OK, OK, OK, NO, NO),
    f"{APP}/observability/traces/{'a' * 16}": (NF, NO, NO, NF, NF, NF, NO, NO),
    f"{PRJ}/imports/runs/unknown": (NF, NF, NO, NF, NF, NF, NO, NO),
    f"{PRJ}/members": (OK, OK, NO, OK, OK, OK, NO, NO),
    "/alerting?project=shop": (OK, OK, NO, OK, OK, OK, NO, NO),
    f"{APP}/observability/alerts": (OK, OK, NO, OK, OK, OK, NO, NO),
    f"{APP}/observability/costs": (OK, OK, OK, OK, OK, OK, OK, NO),
    f"{APP}/security-data": (OK, OK, NO, OK, OK, OK, NO, NO),
    f"{APP}/release/settings": (OK, OK, NO, OK, OK, OK, NO, NO),
    f"{APP}/environments": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{APP}/environments/diff": (OK, OK, NO, OK, OK, OK, OK, NO),
    f"{PRJ}/imports": (OK, OK, NO, OK, OK, OK, NO, NO),
    f"{PRJ}/imports/recommendations": (OK, OK, NO, OK, OK, OK, NO, NO),
    "/security/summary?project=shop": (OK, OK, NO, OK, OK, OK, NO, NO),
}


SUMMARY = {
    "kind": "web",
    "status": "healthy",
    "tiles": {"restarts": 0, "replicas_ready": 1, "replicas_desired": 1},
    "series": {"t": [], "log_info": [], "log_warn": [], "log_error": []},
    "deploys": [],
    "clients": [{"app": "web", "verdict": "denied", "flows": 2}],
    "denied_flows_24h": 2,
    "top_errors": [{"message": "db timeout", "count": 3}],
    "alerts_firing": 0,
}
SUMMARY_TOPOLOGY = (SUMMARY["clients"], SUMMARY["denied_flows_24h"])


class World:
    def __init__(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, expire_on_commit=False)
        self.groups: list[str] = []
        self.role: str | None = None
        with self.Session() as db:
            for name in ("shop", "other"):
                db.add(Project(project_name=name, owner_username="bob"))
                db.add(
                    Deployment(
                        name="web",
                        template_id="static-site",
                        project_id=name,
                        provider_type=ProviderType.KUBERNETES,
                        status=DeploymentStatus.RUNNING,
                        github_repo_url="https://github.com/3-Istor/web",
                    )
                )
            db.commit()

    def be(self, groups, role, owner):
        self.groups, self.role = groups, role
        if owner:
            with self.Session() as db:
                db.query(Project).filter_by(
                    project_name="shop"
                ).one().owner_username = "alice"
                db.commit()


async def _async(value):
    return value


def _stub(value):
    return lambda *a, **k: _async(value)


@pytest.fixture
def world(monkeypatch):
    world = World()
    monkeypatch.setattr(
        keycloak_service,
        "get_project_role",
        lambda user_id, project: world.role if project == "shop" else None,
    )
    listing = lambda user_id: (  # noqa: E731
        [{"name": "shop", "role": world.role}] if world.role else []
    )
    for module in (projects, deployments):
        monkeypatch.setattr(
            module, "fetch_user_projects_from_keycloak", listing
        )
        monkeypatch.setattr(module, "fetch_user_projects_live", listing)
    monkeypatch.setattr(projects, "list_project_members", lambda p: [])
    monkeypatch.setattr(projects, "project_guests_supported", lambda p: True)

    monkeypatch.setattr(
        overview,
        "scope_of",
        lambda d, env="prod": overview.Scope(
            d.project_id,
            d.name,
            f"{d.project_id}-{d.name}",
            TargetCloud.ONPREM,
            ["web"],
            env,
        ),
    )
    monkeypatch.setattr(
        overview,
        "app_metrics",
        _stub(
            {
                "window": "24h",
                "step_seconds": 60,
                "requests_per_second": None,
                "errors_5xx_percent": None,
                "cpu_millicores": None,
                "cpu_request_millicores": None,
            }
        ),
    )
    monkeypatch.setattr(stores, "require", lambda name: None)
    monkeypatch.setattr(
        stores,
        "capabilities",
        lambda: dict.fromkeys(
            ("metrics", "logs", "traces", "alerts", "slo"), 1
        ),
    )
    monkeypatch.setattr(
        signals,
        "golden_signals",
        _stub(
            {
                "range": "24h",
                "step_seconds": 60,
                "no_traffic": True,
                **dict.fromkeys(("rate", "error_percent", "p50_ms"), []),
                "p95_ms": [],
                "routes": [],
            }
        ),
    )
    monkeypatch.setattr(summary, "read_input", lambda *a: None)
    monkeypatch.setattr(summary, "app_summary", _stub(SUMMARY))
    monkeypatch.setattr(apps_health, "read_input", lambda project: None)
    monkeypatch.setattr(
        apps_health,
        "apps_health",
        _stub(
            {
                "apps": [],
                "series": {
                    "t": [],
                    "rps_by_app": {},
                    "err_pct_by_app": {},
                    "error_logs_per_min": [],
                },
            }
        ),
    )
    monkeypatch.setattr(projects, "is_deletion_protected", _stub(True))
    monkeypatch.setattr(projects, "get_customer_access", _stub("closed"))
    monkeypatch.setattr(stores, "trace_account", lambda project: 1)
    monkeypatch.setattr(signals, "slo_status", _stub({"objectives": []}))
    monkeypatch.setattr(app_traces, "list_traces", _stub([]))
    monkeypatch.setattr(app_traces, "trace_detail", _stub(None))
    monkeypatch.setattr(app_logs, "search", _stub([]))
    monkeypatch.setattr(
        app_alerts, "app_alerts", _stub({"alerts": [], "silences": []})
    )
    monkeypatch.setattr(
        app_costs,
        "app_costs",
        _stub(
            {
                "days": 30,
                "cost_eur": 0.0,
                "daily": [],
                "requests": None,
                "metrics_available": False,
                "cost_per_1k_requests_eur": None,
                "usage": None,
                "cpu_idle_ratio": None,
                "ram_idle_ratio": None,
                "recommendations": [],
                "monthly_waste_eur": 0.0,
            }
        ),
    )

    def no_grafana(*a, **k):
        raise RuntimeError("no grafana in tests")

    monkeypatch.setattr(alerting.grafana, "org_id", no_grafana)

    monkeypatch.setattr(
        deployments, "_get_github_token_for_deployment", _stub("gh")
    )
    monkeypatch.setattr(
        deployments, "get_file_content", _stub(("replicas: 1\n", "sha"))
    )
    monkeypatch.setattr(deployments, "_load_values_file", _stub(({}, "sha")))
    monkeypatch.setattr(releases, "_get_github_token_for_deployment", _stub("gh"))
    monkeypatch.setattr(releases, "_load_values_file", _stub(({}, "sha")))
    monkeypatch.setattr(import_service, "ensure_enabled", lambda: None)
    monkeypatch.setattr(import_service, "expire_stale", lambda **k: None)
    return world


@pytest.fixture
def client(world):
    app = FastAPI()
    for module in (
        projects, deployments, app_overview, imports, alerting, releases,
        environments,
    ):
        app.include_router(module.router, prefix="/api")
    app.include_router(app_observability.router, prefix="/api")
    app.include_router(app_observability.projects_router, prefix="/api")
    app.include_router(security.router, prefix="/api")
    app.include_router(finops.router, prefix="/api")

    def get_session():
        db = world.Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[keycloak_service.get_current_user] = lambda: {
        "sub": "uid-alice",
        "preferred_username": "alice",
        "groups": world.groups,
    }
    return TestClient(app)


@pytest.fixture
def session_local(world, monkeypatch):
    # require_project_member opens its own session.
    from app.services import authz

    monkeypatch.setattr(authz, "SessionLocal", world.Session)


@pytest.mark.parametrize("path", list(ROWS))
@pytest.mark.parametrize(
    ("identity", "who"), list(zip(IDENTITIES, IDS)), ids=IDS
)
def test_read_per_identity(client, world, session_local, path, identity, who):
    world.be(*identity)

    response = client.get(f"/api{path}")

    assert response.status_code == ROWS[path][IDS.index(who)], response.text


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ((["finops"], None, False), {"shop", "other"}),
        ((["security-auditor"], None, False), {"shop", "other"}),
        (([], None, False), set()),
        (([], "guest", False), {"shop"}),
    ],
    ids=["finops", "auditor", "none", "guest"],
)
@pytest.mark.parametrize("path", ["/api/projects/", "/api/deployments/"])
def test_listings(client, world, identity, expected, path):
    world.be(*identity)

    rows = client.get(path).json()

    key = "name" if path == "/api/projects/" else "project_id"
    assert {row[key] for row in rows} == expected


def test_platform_listing_carries_no_project_role(client, world):
    world.be(["finops"], None, False)

    roles = {p["name"]: p["role"] for p in client.get("/api/projects/").json()}

    assert roles == {"shop": None, "other": None}


def test_auditor_reads_outputs_in_the_app_list(client, world):
    with world.Session() as db:
        db.get(Deployment, 1).terraform_outputs = '{"app_url": "https://x"}'
        db.commit()
    world.be(["security-auditor"], None, False)

    rows = client.get("/api/deployments/").json()

    assert rows[0]["terraform_outputs"] == '{"app_url": "https://x"}'


def test_owner_outside_the_admins_group_can_edit(client, world):
    world.be([], None, True)

    body = client.get("/api/deployments/1/security-data").json()

    assert body["can_edit"] is True


FIELDS = ("top_errors", "clients", "denied_flows_24h")


@pytest.mark.parametrize(
    ("identity", "shown"),
    [
        ((["security-auditor"], None, False), ([], *SUMMARY_TOPOLOGY)),
        (([], "guest", False), ([], [], 0)),
        (([], "member", False), tuple(SUMMARY[f] for f in FIELDS)),
    ],
    ids=["auditor", "guest", "member"],
)
def test_summary_hides_log_lines_and_callers(client, world, identity, shown):
    world.be(*identity)

    response = client.get("/api/deployments/1/summary")

    body = response.json()
    assert (response.status_code, tuple(body[f] for f in FIELDS)) == (
        200,
        shown,
    )


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (([], "guest", False), (200, [])),
        ((["finops"], None, False), (403, None)),
        (([], None, False), (403, None)),
    ],
    ids=["guest", "finops", "none"],
)
def test_staging_summary_checks_capabilities(
    client, world, monkeypatch, identity, expected
):
    monkeypatch.setattr(runs, "staging_ready", lambda db, deployment_id: True)
    world.be(*identity)

    response = client.get("/api/deployments/1/summary?env=staging")

    errors = response.json().get("top_errors")
    assert (response.status_code, errors) == expected


def _exception(world, critical, monkeypatch):
    with world.Session() as db:
        db.add(
            SecurityException(
                project="shop",
                app="web",
                fingerprint="f1",
                rule="CVE-1",
                kind="vulnerability",
                status="not_affected",
                statement="never called",
                expires_on=date.today() + timedelta(days=30),
                author="dev",
            )
        )
        db.commit()
    tier = Tier.CORE if critical else Tier.IMPORTANT
    monkeypatch.setattr(security, "_exception_tier", lambda db, row: tier)
    monkeypatch.setattr(security, "_sync_exceptions_file", _stub(None))


#         infra auditor finops owner admin member guest none
BELOW = (OK, NO, NO, OK, OK, NO, NO, NO)
# Revoking: the auditor may only act on a critical exception.
REVOKE_CRITICAL = (OK, OK, NO, OK, OK, NO, NO, NO)
# Approving a critical is the auditor's or a platform admin's alone.
APPROVE_CRITICAL = (OK, OK, NO, NO, NO, NO, NO, NO)


@pytest.mark.parametrize(
    ("critical", "rows"),
    [(False, BELOW), (True, APPROVE_CRITICAL)],
    ids=["below-critical", "critical"],
)
@pytest.mark.parametrize(
    ("identity", "who"), list(zip(IDENTITIES, IDS)), ids=IDS
)
def test_approve_exception(
    client, world, session_local, monkeypatch, critical, rows, identity, who
):
    _exception(world, critical, monkeypatch)
    world.be(*identity)

    response = client.post("/api/security/exceptions/1/approve")

    assert response.status_code == rows[IDS.index(who)], response.text


@pytest.mark.parametrize("critical", [False, True], ids=["below", "critical"])
@pytest.mark.parametrize(
    ("identity", "who"), list(zip(IDENTITIES, IDS)), ids=IDS
)
def test_revoke_exception(
    client, world, session_local, monkeypatch, critical, identity, who
):
    _exception(world, critical, monkeypatch)
    world.be(*identity)

    response = client.delete("/api/security/exceptions/1")

    expected = REVOKE_CRITICAL if critical else BELOW
    assert response.status_code == expected[IDS.index(who)], response.text


@pytest.mark.parametrize(
    ("identity", "who"), list(zip(IDENTITIES, IDS)), ids=IDS
)
def test_put_budget(client, world, monkeypatch, identity, who):
    monkeypatch.setattr(finops, "_load_deployments", lambda *a, **k: [])
    provider = type(
        "P",
        (),
        {
            "specs": lambda self, d: [],
            "summary": lambda self, s: {"month_to_date_eur": 0.0},
        },
    )()
    monkeypatch.setattr(finops, "get_cost_provider", lambda: provider)
    world.be(*identity)

    response = client.put(
        "/api/finops/budgets/shop", json={"monthly_amount_eur": 100}
    )

    expected = (OK, NO, NO, OK, NO, NO, NO, NO)
    assert response.status_code == expected[IDS.index(who)], response.text
