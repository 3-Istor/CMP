"""
The app observability API without the stores: who may read, what reaches
the stores (the scope comes from the app's row, never from the request),
the bounds on ranges and sizes, and "not available yet" when a store is not
configured.
"""

from datetime import datetime, timedelta, timezone
from unittest import mock
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.observability import TraceTenant
from app.models.project import Project, TargetCloud
from app.models.release import ReleaseEvent
from app.routers import app_observability as router
from app.services import authz, keycloak_service
from app.services.keycloak_service import get_current_user
from app.services.observability import app_costs, silences, stores

SHOP, RIVAL = 1, 2
SHOP_ACCOUNT, RIVAL_ACCOUNT = 11, 12
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TOKENS = {
    "member": {"sub": "uid-alice", "preferred_username": "alice"},
    "guest": {"sub": "uid-bob", "preferred_username": "bob"},
    "outsider": {"sub": "uid-eve", "preferred_username": "eve"},
    "finops": {
        "sub": "uid-fin",
        "preferred_username": "fin",
        "groups": ["/finops"],
    },
}
ROLES = {("uid-alice", "shop"): "member", ("uid-bob", "shop"): "guest"}
NOW = datetime.now(timezone.utc)


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine)
    with made() as db:
        for project in ("shop", "rival"):
            db.add(Project(project_name=project, owner_username="owner"))
        for app_id, project in ((SHOP, "shop"), (RIVAL, "rival")):
            db.add(
                Deployment(
                    id=app_id,
                    name="web",
                    template_id="k3s-gitops-app",
                    project_id=project,
                    provider_type=ProviderType.KUBERNETES,
                    status=DeploymentStatus.RUNNING,
                )
            )
        db.add(TraceTenant(id=SHOP_ACCOUNT, project="shop"))
        db.add(TraceTenant(id=RIVAL_ACCOUNT, project="rival"))
        db.commit()
    return made


class Stores:
    """Every request the stores receive, answered by *answer*."""

    def __init__(self):
        self.sent: list[httpx.Request] = []
        self.answer = lambda request: httpx.Response(200, json={})

    def client(self, base_url: str) -> httpx.AsyncClient:
        def respond(request: httpx.Request) -> httpx.Response:
            self.sent.append(request)
            return self.answer(request)

        return httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(respond)
        )

    def form(self, i: int = -1) -> dict[str, list[str]]:
        return parse_qs(self.sent[i].content.decode())


@pytest.fixture
def fake():
    return Stores()


@pytest.fixture
def make(factory, fake):
    started = []

    def build(who="member", configured=True):
        app = FastAPI()
        app.include_router(router.router, prefix="/api")

        def override_db():
            with factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: TOKENS[who]
        url = "http://store.test" if configured else ""
        patches = [
            mock.patch.object(authz, "SessionLocal", factory),
            mock.patch.object(stores, "SessionLocal", factory),
            mock.patch.object(
                keycloak_service,
                "get_project_role",
                lambda user, project: ROLES.get((user, project)),
            ),
            mock.patch.object(
                authz, "get_user_id_from_token", lambda t: t["sub"]
            ),
            mock.patch.object(stores, "_client", fake.client),
            mock.patch.object(
                silences, "_client", lambda: fake.client("http://am.test")
            ),
            mock.patch.object(stores.settings, "OBS_V3_ENABLED", configured),
            mock.patch.object(stores.settings, "OBS_VMSINGLE_URL", url),
            mock.patch.object(stores.settings, "OBS_VLOGS_URL", url),
            mock.patch.object(stores.settings, "OBS_VTRACES_URL", url),
            mock.patch.object(
                stores.settings, "OBS_TRACES_ROUTING_ENABLED", configured
            ),
        ]
        for patch in patches:
            patch.start()
        started.extend(patches)
        return TestClient(app)

    yield build
    for patch in started:
        patch.stop()


def _get(client, app_id, path, **params):
    return client.get(
        f"/api/deployments/{app_id}/observability{path}", params=params
    )


READS = [
    "",
    "/signals",
    "/slo",
    "/deploys",
    "/dora",
    "/logs/top-errors",
    "/logs",
    "/traces",
    f"/traces/{TRACE}",
    "/alerts",
]


# ── Rights ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", READS + ["/costs"])
def test_a_member_of_another_project_gets_403(make, fake, path):
    client = make("member")

    response = _get(client, RIVAL, path)

    assert (response.status_code, fake.sent) == (403, [])


@pytest.mark.parametrize("path", READS + ["/costs"])
def test_someone_in_no_project_gets_403(make, fake, path):
    client = make("outsider")

    response = _get(client, SHOP, path)

    assert (response.status_code, fake.sent) == (403, [])


@pytest.mark.parametrize("path", READS)
def test_a_guest_does_not_read_signals_logs_or_traces(make, fake, path):
    client = make("guest")

    response = _get(client, SHOP, path)

    assert (response.status_code, fake.sent) == (403, [])


@pytest.mark.parametrize("path", READS)
def test_an_unknown_app_answers_404(make, path):
    client = make("member")

    response = _get(client, 999, path)

    assert response.status_code == 404


def test_finops_reads_an_apps_costs_without_being_a_member(make, fake):
    client = make("finops")
    with mock.patch.object(
        app_costs, "_cost", return_value=(0.0, [], [])
    ), mock.patch.object(app_costs, "_usage", return_value=None):
        fake.answer = lambda r: httpx.Response(
            200, json={"data": {"result": []}}
        )
        response = _get(client, RIVAL, "/costs")

    assert response.status_code == 200


def test_finops_does_not_read_an_apps_logs(make):
    client = make("finops")

    response = _get(client, SHOP, "/logs")

    assert response.status_code == 403


# ── Scope pinned server side ─────────────────────────────────────────────────


def _empty_metrics(request):
    return httpx.Response(200, json={"data": {"result": []}})


def test_metrics_carry_the_apps_namespace_filters(make, fake):
    fake.answer = _empty_metrics
    client = make("member")

    _get(client, SHOP, "/signals", namespace="rival-web")

    assert fake.form(0)["extra_filters[]"] == [
        '{cluster=~"onprem|", namespace="shop-web"}',
        '{cluster=~"onprem|", namespace="envoy-gateway-system", '
        'envoy_cluster_name=~"httproute/shop-web/.+"}',
    ]


def test_a_client_extra_filter_never_reaches_the_store(make, fake):
    fake.answer = _empty_metrics
    client = make("member")

    _get(client, SHOP, "/signals", **{"extra_filters[]": "{}"})

    assert "{}" not in fake.form(0)["extra_filters[]"]


def _logs(request):
    return httpx.Response(200, text="")


def test_logs_are_pinned_to_the_apps_namespace(make, fake):
    fake.answer = _logs
    client = make("member")

    _get(client, SHOP, "/logs", namespace="rival-web", q="x")

    assert fake.form()["extra_stream_filters"] == ['{namespace="shop-web"}']


def test_logs_read_the_single_logs_tenant(make, fake):
    fake.answer = _logs
    client = make("member")

    _get(client, SHOP, "/logs", headers="AccountID: 9")

    assert fake.sent[-1].headers["AccountID"] == "0"


def test_a_search_phrase_is_sent_quoted(make, fake):
    fake.answer = _logs
    client = make("member")

    _get(client, SHOP, "/logs", q='a" | delete _msg')

    assert '"a\\" | delete _msg"' in fake.form()["query"][0]


def _jaeger(request):
    return httpx.Response(200, json={"data": []})


def test_traces_use_the_projects_own_account(make, fake):
    fake.answer = _jaeger
    client = make("member")

    client.get(
        f"/api/deployments/{SHOP}/observability/traces",
        headers={"AccountID": str(RIVAL_ACCOUNT)},
        params={"account": RIVAL_ACCOUNT},
    )

    assert fake.sent[-1].headers["AccountID"] == str(SHOP_ACCOUNT)


def test_a_trace_detail_uses_the_projects_own_account(make, fake):
    fake.answer = lambda r: httpx.Response(404)
    client = make("member")

    response = _get(client, SHOP, f"/traces/{TRACE}")

    assert (response.status_code, fake.sent[-1].headers["AccountID"]) == (
        404,
        str(SHOP_ACCOUNT),
    )


def test_a_project_without_a_trace_tenant_has_no_traces_yet(
    make, factory, fake
):
    with factory() as db:
        db.query(TraceTenant).filter_by(project="shop").delete()
        db.commit()
    client = make("member")

    response = _get(client, SHOP, "/traces")

    assert (response.status_code, fake.sent) == (503, [])


def test_alerts_are_asked_for_the_apps_project_and_name(make, fake):
    fake.answer = lambda r: httpx.Response(200, json=[])
    client = make("member")

    _get(client, SHOP, "/alerts", project="rival")

    assert fake.sent[0].url.params.get_list("filter") == [
        'cnp_project="shop"',
        'cnp_app="web"',
    ]


def test_alerts_of_another_app_are_dropped_even_if_returned(make, fake):
    other = {
        "labels": {"cnp_project": "rival", "cnp_app": "web", "alertname": "X"},
        "annotations": {},
        "startsAt": NOW.isoformat(),
        "status": {"state": "active"},
        "fingerprint": "f",
    }
    fake.answer = lambda r: httpx.Response(200, json=[other])
    client = make("member")

    body = _get(client, SHOP, "/alerts").json()

    assert body["alerts"] == []


# ── Bounds ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,params",
    [
        ("/signals", {"range": "30d"}),
        ("/slo", {"range": "90d"}),
        ("/logs", {"range": "30d"}),
        ("/logs", {"limit": 501}),
        ("/logs", {"limit": 0}),
        ("/logs", {"q": "x" * 201}),
        ("/logs", {"level": "fatal"}),
        ("/logs", {"trace_id": "not-hex"}),
        ("/logs/top-errors", {"limit": 21}),
        ("/traces", {"limit": 51}),
        ("/traces", {"range": "7d"}),
        ("/traces", {"min_duration_ms": 0}),
        ("/dora", {"days": 365}),
    ],
)
def test_ranges_and_sizes_are_bounded(make, fake, path, params):
    client = make("member")

    response = _get(client, SHOP, path, **params)

    assert (response.status_code, fake.sent) == (422, [])


def test_a_trace_id_must_be_hex(make, fake):
    client = make("member")

    response = _get(client, SHOP, "/traces/..%2F..%2Fservices")

    assert response.status_code in (404, 422) and fake.sent == []


def test_control_characters_are_refused_in_a_search(make, fake):
    client = make("member")

    response = _get(client, SHOP, "/logs", q="a\nb")

    assert (response.status_code, fake.sent) == (422, [])


# ── Not configured ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,signal",
    [
        ("/signals", "metrics"),
        ("/slo", "slo"),
        ("/logs", "logs"),
        ("/logs/top-errors", "logs"),
        ("/traces", "traces"),
        (f"/traces/{TRACE}", "traces"),
        ("/alerts", "alerts"),
    ],
)
def test_an_unconfigured_store_answers_not_available(make, fake, path, signal):
    client = make("member", configured=False)

    response = _get(client, SHOP, path)

    assert (response.status_code, response.json()["detail"], fake.sent) == (
        503,
        {"code": "not_available", "signal": signal},
        [],
    )


def test_capabilities_say_what_is_configured(make):
    client = make("member", configured=False)

    body = _get(client, SHOP, "").json()

    assert {k: body[k] for k in ("metrics", "logs", "traces", "alerts")} == {
        "metrics": False,
        "logs": False,
        "traces": False,
        "alerts": False,
    }


def test_logs_capability_is_off_for_a_cloud_app(make, factory):
    with factory() as db:
        db.get(Deployment, SHOP).app_config = '{"target_cloud": "aws"}'
        db.query(Project).filter_by(project_name="shop").one().target_cloud = (
            TargetCloud.AWS
        )
        db.commit()
    client = make("member")

    body = _get(client, SHOP, "").json()

    assert (body["logs"], body["metrics"]) == (False, True)


def test_logs_capability_is_on_for_an_onprem_app(make):
    client = make("member")

    assert _get(client, SHOP, "").json()["logs"] is True


def test_capabilities_link_the_app_dashboard(make):
    client = make("member")

    with mock.patch.object(
        router.alerts_grafana, "org_id", mock.AsyncMock(return_value=7)
    ):
        body = _get(client, SHOP, "").json()

    assert body["grafana_url"] == (
        "https://grafana.3istor.com/d/cnp-app-signals"
        "?orgId=7&var-app=web&var-namespace=shop-web"
    )


def test_capabilities_fall_back_to_grafana_without_an_org(make):
    client = make("member")

    with mock.patch.object(
        router.alerts_grafana, "org_id", mock.AsyncMock(side_effect=OSError)
    ):
        body = _get(client, SHOP, "").json()

    assert body["grafana_url"] == router.GRAFANA_BASE_URL


def test_a_store_that_does_not_answer_is_a_502(make, fake):
    fake.answer = lambda r: httpx.Response(500)
    client = make("member")

    response = _get(client, SHOP, "/signals")

    assert response.status_code == 502


# ── Delivery from the release history ────────────────────────────────────────


def _event(factory, outcome, days_ago, environment="prod", app=SHOP):
    with factory() as db:
        db.add(
            ReleaseEvent(
                deployment_id=app,
                revision=1,
                image="ghcr.io/3-istor/web:1.4.0@sha256:abc",
                strategy="canary",
                started_at=NOW - timedelta(days=days_ago),
                outcome=outcome,
                environment=environment,
            )
        )
        db.commit()


def test_dora_counts_prod_deploys_and_failed_changes(make, factory):
    for outcome in ("promoted", "deployed", "promoted", "aborted_auto"):
        _event(factory, outcome, 2)
    _event(factory, "superseded", 2)
    _event(factory, "promoted", 2, environment="staging")
    _event(factory, "promoted", 40)
    client = make("member")

    body = _get(client, SHOP, "/dora", days=30).json()

    assert (body["deployments"], body["failed_changes"]) == (3, 1)


def test_change_failure_rate_is_failures_over_attempts(make, factory):
    for outcome in ("promoted", "failed", "aborted_manual", "deployed"):
        _event(factory, outcome, 1)
    client = make("member")

    body = _get(client, SHOP, "/dora", days=7).json()

    assert body["change_failure_rate"] == 0.5


def test_deploy_markers_carry_the_version_and_outcome(make, factory):
    _event(factory, "promoted", 0.1)
    client = make("member")

    body = _get(client, SHOP, "/deploys", range="24h").json()

    assert [(m["version"], m["outcome"]) for m in body] == [
        ("1.4.0", "promoted")
    ]


def test_deploy_markers_never_include_another_apps(make, factory):
    _event(factory, "promoted", 0.1, app=RIVAL)
    client = make("member")

    body = _get(client, SHOP, "/deploys", range="24h").json()

    assert body == []


# ── Costs ────────────────────────────────────────────────────────────────────


def test_cost_per_1000_requests_divides_cost_by_envoy_requests(make, fake):
    fake.answer = lambda r: httpx.Response(
        200,
        json={"data": {"result": [{"metric": {}, "value": [0, "200000"]}]}},
    )
    client = make("member")
    with mock.patch.object(
        app_costs, "_cost", return_value=(4.0, [], [])
    ), mock.patch.object(app_costs, "_usage", return_value=None):
        body = _get(client, SHOP, "/costs").json()

    assert body["cost_per_1k_requests_eur"] == 0.02


def test_waste_compares_requests_with_measured_usage(make, fake):
    fake.answer = _empty_metrics
    usage = {
        "cpu_request_cores": 1.0,
        "cpu_p95_cores": 0.25,
        "ram_request_gib": 2.0,
        "ram_p99_gib": 1.0,
        "history_days": 14.0,
    }
    client = make("member")
    with mock.patch.object(
        app_costs, "_cost", return_value=(4.0, [], [])
    ), mock.patch.object(app_costs, "_usage", return_value=usage):
        body = _get(client, SHOP, "/costs").json()

    assert (body["cpu_idle_ratio"], body["ram_idle_ratio"]) == (0.75, 0.5)


def test_costs_without_traffic_have_no_cost_per_request(make, fake):
    fake.answer = _empty_metrics
    client = make("member")
    with mock.patch.object(
        app_costs, "_cost", return_value=(4.0, [], [])
    ), mock.patch.object(app_costs, "_usage", return_value=None):
        body = _get(client, SHOP, "/costs").json()

    assert body["cost_per_1k_requests_eur"] is None
