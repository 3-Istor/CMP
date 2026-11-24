"""
The app observability API against the real binaries: VictoriaMetrics,
VictoriaLogs, VictoriaTraces and Alertmanager seeded with two projects
whose apps carry the same name (tests/obs_seed.py). A member of "shop" reads
the signals of shop's app, and nothing of "rival", whatever they send.
"""

import asyncio
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

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
from app.models.project import Project
from app.routers import app_observability as router
from app.services import authz, keycloak_service
from app.services.keycloak_service import get_current_user
from app.services.observability import grafana_dashboards as gd
from app.services.observability import silences, stores
from tests import obs_seed
from tests.obs_stack import free_port, need, store, wait_for_trace

SHOP_APP, RIVAL_APP, IDLE_APP = 1, 2, 3
ALICE = {"sub": "uid-alice", "preferred_username": "alice"}


def _alertmanager(data) -> tuple[subprocess.Popen, str] | None:
    path = os.environ.get("ALERTMANAGER") or shutil.which("alertmanager")
    if not path:
        return None
    port = free_port()
    config = data / "alertmanager.yml"
    config.write_text("route:\n  receiver: none\nreceivers:\n  - name: none\n")
    process = subprocess.Popen(
        [
            path,
            f"--config.file={config}",
            f"--storage.path={data / 'am'}",
            f"--web.listen-address=127.0.0.1:{port}",
            "--cluster.listen-address=",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/-/ready").status_code == 200:
                return process, url
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    process.kill()
    pytest.fail("alertmanager did not start")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    need("victoria-metrics", "victoria-logs", "victoria-traces")
    data = tmp_path_factory.mktemp("portal")
    metrics = store("victoria-metrics", data)
    logs = store("victoria-logs", data)
    traces = store("victoria-traces", data)
    am = _alertmanager(data)
    obs_seed.seed(metrics.url, logs.url, traces.url, am[1] if am else None)
    for trace, project in (
        (obs_seed.SHOP_TRACE, "shop"),
        (obs_seed.RIVAL_TRACE, "rival"),
    ):
        wait_for_trace(traces.url, trace, obs_seed.APPS[project]["account"])
    yield {
        "metrics": metrics.url,
        "logs": logs.url,
        "traces": traces.url,
        "alertmanager": am[1] if am else None,
    }
    for process in (metrics, logs, traces):
        process.stop()
    if am:
        am[0].kill()


@pytest.fixture
def api(stack):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for project in ("shop", "rival"):
            db.add(Project(project_name=project, owner_username="owner"))
        for app_id, name, project in (
            (SHOP_APP, "web", "shop"),
            (RIVAL_APP, "web", "rival"),
            (IDLE_APP, "idle", "shop"),
        ):
            db.add(
                Deployment(
                    id=app_id,
                    name=name,
                    template_id="k3s-gitops-app",
                    project_id=project,
                    provider_type=ProviderType.KUBERNETES,
                    status=DeploymentStatus.RUNNING,
                )
            )
        for project, spec in obs_seed.APPS.items():
            db.add(TraceTenant(id=spec["account"], project=project))
        db.commit()
    app = FastAPI()
    app.include_router(router.router, prefix="/api")

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: ALICE
    am = stack["alertmanager"] or "http://127.0.0.1:9"
    patches = [
        mock.patch.object(authz, "SessionLocal", factory),
        mock.patch.object(stores, "SessionLocal", factory),
        mock.patch.object(
            keycloak_service,
            "get_project_role",
            lambda user, project: "member" if project == "shop" else None,
        ),
        mock.patch.object(
            keycloak_service,
            "get_user_id_from_token",
            lambda token: token["sub"],
        ),
        mock.patch.object(
            authz, "get_user_id_from_token", lambda token: token["sub"]
        ),
        mock.patch.object(stores.settings, "OBS_V3_ENABLED", True),
        mock.patch.object(
            stores.settings, "OBS_VMSINGLE_URL", stack["metrics"]
        ),
        mock.patch.object(stores.settings, "OBS_VLOGS_URL", stack["logs"]),
        mock.patch.object(stores.settings, "OBS_VTRACES_URL", stack["traces"]),
        mock.patch.object(stores.settings, "OBS_TRACES_ROUTING_ENABLED", True),
        mock.patch.object(stores.settings, "ALERTMANAGER_URL", am),
        mock.patch.object(silences.settings, "ALERTMANAGER_URL", am),
    ]
    for patch in patches:
        patch.start()
    yield TestClient(app)
    for patch in patches:
        patch.stop()


def _get(api, app_id, path, **params):
    return api.get(
        f"/api/deployments/{app_id}/observability{path}", params=params
    )


# ── Metrics ──────────────────────────────────────────────────────────────────


def test_golden_signals_are_the_apps_own_routes(api):
    body = _get(api, SHOP_APP, "/signals", range="1h").json()

    assert {r["route"] for r in body["routes"]} == {
        "web/rule/0",
        "web-api/rule/0",
    }


def test_golden_signals_carry_rate_errors_and_latency(api):
    body = _get(api, SHOP_APP, "/signals", range="1h").json()

    assert (
        body["no_traffic"],
        bool(body["rate"]),
        bool(body["error_percent"]),
        bool(body["p95_ms"]),
    ) == (False, True, True, True)


def test_latency_percentiles_come_from_the_envoy_histogram(api):
    route = _get(api, SHOP_APP, "/signals", range="1h").json()["routes"][0]

    assert 0 < route["p50_ms"] < route["p95_ms"] <= 1000


def test_an_app_without_envoy_series_has_no_traffic_yet(api):
    body = _get(api, IDLE_APP, "/signals", range="1h").json()

    assert (body["no_traffic"], body["routes"]) == (True, [])


def test_a_query_naming_another_namespace_reads_nothing(api):
    scope = stores.Scope("shop", "web", "shop-web", "onprem")
    query = (
        'sum(envoy_cluster_upstream_rq_total{envoy_cluster_name=~"'
        'httproute/rival-web/.+"})'
    )

    rows = asyncio.run(
        stores.instant(scope, query, datetime.now(timezone.utc))
    )

    assert rows == []


def test_slo_status_reads_the_budget_the_rules_record(api):
    body = _get(api, SHOP_APP, "/slo", range="1h").json()

    left = {o["kind"]: o["budget_remaining"] for o in body["objectives"]}
    assert round(left["availability"], 2) == 0.62


def test_an_app_without_requests_keeps_its_whole_budget(api):
    body = _get(api, IDLE_APP, "/slo", range="1h").json()

    assert [
        (o["budget_remaining"], o["insufficient_traffic"])
        for o in body["objectives"]
    ] == [(1.0, True), (1.0, True)]


def test_slo_burn_rate_is_the_error_ratio_over_the_budget(api):
    body = _get(api, SHOP_APP, "/slo", range="1h").json()

    burn = {o["kind"]: o["burn_rate_1h"] for o in body["objectives"]}
    assert round(burn["availability"], 1) == 2.4


# ── Logs ─────────────────────────────────────────────────────────────────────


def test_top_errors_fold_numbers_into_one_pattern(api):
    rows = _get(api, SHOP_APP, "/logs/top-errors", range="1h").json()

    assert rows[0]["pattern"] == "payment provider timeout after <N> ms"


def test_top_errors_count_every_line_of_a_pattern(api):
    rows = _get(api, SHOP_APP, "/logs/top-errors", range="1h").json()

    assert rows[0]["count"] == 12


def test_top_errors_link_to_a_trace_when_lines_carry_one(api):
    rows = _get(api, SHOP_APP, "/logs/top-errors", range="1h").json()

    assert rows[0]["trace_id"] == obs_seed.SHOP_TRACE


def test_top_errors_count_raw_lines_with_error_words(api):
    rows = _get(api, SHOP_APP, "/logs/top-errors", range="1h").json()

    assert any(r["pattern"].startswith("Traceback") for r in rows)


def test_top_errors_never_include_another_projects_lines(api):
    rows = _get(api, SHOP_APP, "/logs/top-errors", range="24h").json()

    assert not any("rival" in r["pattern"] for r in rows)


def test_search_finds_the_apps_lines_by_phrase(api):
    rows = _get(api, SHOP_APP, "/logs", q="timeout after 3001").json()

    assert [r["message"] for r in rows] == [
        "payment provider timeout after 3001 ms"
    ]


def test_search_for_another_projects_text_finds_nothing(api):
    rows = _get(api, SHOP_APP, "/logs", q=obs_seed.RIVAL_SECRET).json()

    assert rows == []


@pytest.mark.parametrize(
    "attempt",
    [
        '" | delete _msg | "',
        '"} or {namespace="rival-web"',
        'rival card number 4111" or "',
        "* or namespace:rival-web",
    ],
)
def test_a_crafted_phrase_stays_a_phrase(api, attempt):
    response = _get(api, SHOP_APP, "/logs", q=attempt, range="24h")

    assert (response.status_code, response.json()) == (200, [])


def test_search_by_trace_returns_the_lines_of_that_trace(api):
    rows = _get(
        api, SHOP_APP, "/logs", trace_id=obs_seed.SHOP_TRACE, range="1h"
    ).json()

    assert {r["trace_id"] for r in rows} == {obs_seed.SHOP_TRACE}


def test_another_projects_trace_id_finds_no_log_line(api):
    rows = _get(
        api, SHOP_APP, "/logs", trace_id=obs_seed.RIVAL_TRACE, range="24h"
    ).json()

    assert rows == []


def test_search_honours_the_limit(api):
    rows = _get(api, SHOP_APP, "/logs", range="1h", limit=3).json()

    assert len(rows) == 3


def test_search_filters_by_level_whatever_its_case(api):
    rows = _get(api, SHOP_APP, "/logs", range="1h", level="error").json()

    assert any(r["message"].startswith("order ") for r in rows)


# ── Traces ───────────────────────────────────────────────────────────────────


def test_trace_list_holds_the_apps_traces(api):
    rows = _get(api, SHOP_APP, "/traces", range="1h").json()

    assert {r["trace_id"] for r in rows} == {
        obs_seed.SHOP_TRACE,
        obs_seed.SHOP_SLOW_TRACE,
    }


def test_trace_list_can_keep_only_traces_with_errors(api):
    rows = _get(api, SHOP_APP, "/traces", range="1h", errors_only=True)

    assert [r["trace_id"] for r in rows.json()] == [obs_seed.SHOP_TRACE]


def test_trace_detail_returns_the_span_tree(api):
    body = _get(api, SHOP_APP, f"/traces/{obs_seed.SHOP_TRACE}").json()

    parents = {s["operation"]: s["parent_id"] for s in body["span_list"]}
    assert parents == {
        "POST /api/pay": None,
        "SELECT orders": "a000000000000001",
        "POST payment-provider": "a000000000000001",
    }


def test_another_projects_trace_id_is_not_found(api):
    response = _get(api, SHOP_APP, f"/traces/{obs_seed.RIVAL_TRACE}")

    assert response.status_code == 404


def test_the_rival_app_answers_403_to_a_shop_member(api):
    response = _get(api, RIVAL_APP, f"/traces/{obs_seed.RIVAL_TRACE}")

    assert response.status_code == 403


# ── Alerts ───────────────────────────────────────────────────────────────────


def test_alerts_are_the_apps_own(api, stack):
    if not stack["alertmanager"]:
        pytest.skip("not installed: alertmanager")

    body = _get(api, SHOP_APP, "/alerts").json()

    assert [a["labels"]["alertname"] for a in body["alerts"]] == [
        "SloAvailabilityBurnRate"
    ]


def test_a_silence_from_the_portal_covers_the_apps_alert(api, stack):
    if not stack["alertmanager"]:
        pytest.skip("not installed: alertmanager")
    body = silences.build(
        "shop",
        {"cnp_app": "web", "alertname": "SloAvailabilityBurnRate"},
        timedelta(hours=1),
        "alice",
        "",
        datetime.now(timezone.utc),
    )
    asyncio.run(silences.create(body))
    time.sleep(0.5)

    found = _get(api, SHOP_APP, "/alerts").json()

    assert (found["alerts"][0]["state"], len(found["silences"])) == (
        "suppressed",
        1,
    )


# ── Grafana dashboards ───────────────────────────────────────────────────────


def _panel_queries():
    builders = [gd.home_dashboard] + [build for _, build in gd.DASHBOARDS]
    for dashboard in (build() for build in builders):
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                yield dashboard["uid"], panel["title"], target["expr"]


@pytest.mark.parametrize("uid,title,expr", list(_panel_queries()))
def test_every_dashboard_query_runs_on_victoriametrics(
    stack, uid, title, expr
):
    query = (
        expr.replace("$app", "web")
        .replace("$env", "shop-web")
        .replace("$__range_s", "86400")
        .replace("$__range", "24h")
    )

    response = httpx.get(
        f"{stack['metrics']}/api/v1/query", params={"query": query}
    )

    assert (uid, title, response.status_code) == (uid, title, 200)


def test_the_budget_panel_reads_the_recorded_series(stack):
    budget = gd.budget_left(gd.APP_SEL)

    found = httpx.get(
        f"{stack['metrics']}/api/v1/query",
        params={
            "query": budget.replace("$app", "web").replace("$env", "shop-web")
        },
    ).json()["data"]["result"]

    assert {r["metric"]["cnp_slo"] for r in found} == {
        "availability",
        "latency",
    }
