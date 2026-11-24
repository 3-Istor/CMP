"""
The CMP's objects in each project's Grafana org (D17): the traces
datasource through vmauth as the project's user, and the dashboards per
role, written only when they changed.
"""

import asyncio
import json
from unittest import mock

import httpx
import pytest

from app.services import grafana_service, metrics_isolation
from app.services.observability import grafana_dashboards as gd
from app.services.observability import grafana_project as gp
from app.services.observability.slo import BUDGET_LOOKBACK


@pytest.fixture(autouse=True)
def configured():
    gp._applied.clear()  # pylint: disable=protected-access
    with (
        mock.patch.object(gp.settings, "METRICS_PROJECT_KEY", "k" * 64),
        mock.patch.object(
            gp.settings, "OBS_VTRACES_URL", "http://vtraces.test"
        ),
        mock.patch.object(gp.settings, "OBS_VLOGS_URL", "http://vlogs.test"),
        mock.patch.object(gp.settings, "GRAFANA_ADMIN_PASSWORD", "x"),
        mock.patch.object(
            gp.settings, "OBS_GRAFANA_PROVISIONING_ENABLED", True
        ),
    ):
        yield


class FakeGrafana:
    def __init__(self, existing=()):
        self.sent: list[httpx.Request] = []
        self.existing = set(existing)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        path = request.url.path
        if path == "/api/orgs/name/Project Shop":
            return httpx.Response(200, json={"id": 42})
        if path.startswith("/api/orgs/name/"):
            return httpx.Response(404)
        if path == "/api/org/preferences":
            return httpx.Response(200, json={})
        if request.method == "GET":
            found = path.rsplit("/", 1)[-1] in self.existing
            return httpx.Response(200 if found else 404, json={})
        return httpx.Response(200, json={})

    def writes(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.sent if r.method != "GET"]


def _sync(fake: FakeGrafana, project: str = "shop") -> bool:
    async def run():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handler),
            base_url="http://grafana.test",
        )
        with (
            mock.patch.object(gp, "_get_http_client", lambda: client),
            mock.patch.object(
                grafana_service, "_get_http_client", lambda: client
            ),
            mock.patch.object(
                grafana_service, "GRAFANA_BASE_URL", "http://grafana.test"
            ),
        ):
            return await gp.sync_project(project)

    return asyncio.run(run())


def test_the_traces_datasource_reads_through_vmauth_as_the_project():
    ds = gp.traces_datasource("shop")

    assert (ds["url"], ds["basicAuthUser"]) == (
        f"{gp.settings.OBS_VMAUTH_PROJECTS_URL}/select/jaeger",
        "project-shop",
    )


def test_the_traces_datasource_password_is_the_projects_vmauth_one():
    ds = gp.traces_datasource("shop")

    assert ds["secureJsonData"]["basicAuthPassword"] == (
        metrics_isolation.password("shop")
    )


def test_no_traces_datasource_before_the_traces_store_exists():
    with mock.patch.object(gp.settings, "OBS_VTRACES_URL", ""):
        wanted = gp.desired("shop")

    assert "datasource" not in wanted


def test_a_first_sync_writes_folders_datasources_dashboards_and_home():
    fake = FakeGrafana()

    _sync(fake)

    writes = fake.writes()
    assert (
        writes.count(("POST", "/api/folders")),
        writes.count(("POST", "/api/datasources")),
        writes.count(("POST", "/api/dashboards/db")),
        writes[-1],
    ) == (
        len(gd.FOLDERS),
        2,
        len(gp.desired("shop")["dashboards"]),
        ("PUT", "/api/org/preferences"),
    )


def test_the_project_health_dashboard_is_set_as_the_org_home():
    fake = FakeGrafana()

    _sync(fake)

    prefs = next(r for r in fake.sent if r.url.path == "/api/org/preferences")
    assert json.loads(prefs.content) == {"homeDashboardUID": gd.HOME_UID}


def test_the_logs_datasource_reads_through_vmauth_as_the_project():
    ds = gp.logs_datasource("shop")

    assert (ds["url"], ds["basicAuthUser"], ds["type"]) == (
        gp.settings.OBS_VMAUTH_PROJECTS_URL,
        "project-shop",
        "victoriametrics-logs-datasource",
    )


def test_no_logs_datasource_or_explorer_before_the_logs_store_exists():
    with mock.patch.object(gp.settings, "OBS_VLOGS_URL", ""):
        wanted = gp.desired("shop")

    uids = {d["uid"] for _, d in wanted["dashboards"]}
    assert (len(wanted["datasources"]), gd.LOG_EXPLORER_UID in uids) == (
        1,
        False,
    )


def test_every_write_targets_the_projects_own_org():
    fake = FakeGrafana()

    _sync(fake)

    orgs = {
        r.headers.get("X-Grafana-Org-Id")
        for r in fake.sent
        if not r.url.path.startswith("/api/orgs/")
    }
    assert orgs == {"42"}


def test_an_existing_datasource_is_replaced_not_duplicated():
    fake = FakeGrafana(existing={"cnp-traces-shop"})

    _sync(fake)

    assert ("PUT", "/api/datasources/uid/cnp-traces-shop") in fake.writes()


def test_an_unchanged_project_is_not_written_twice():
    fake = FakeGrafana()
    _sync(fake)
    fake.sent.clear()

    _sync(fake)

    assert fake.sent == []


def test_a_project_without_an_org_yet_is_left_for_later():
    fake = FakeGrafana()

    written = _sync(fake, "neworg")

    assert (written, fake.writes()) == (False, [])


def test_nothing_is_written_with_the_flag_off():
    with (
        mock.patch.object(
            gp.settings, "OBS_GRAFANA_PROVISIONING_ENABLED", False
        ),
        mock.patch.object(gp, "sync_project") as sync,
    ):
        asyncio.run(gp.sync_quietly())

    sync.assert_not_called()


def _all_dashboards() -> list[dict]:
    return [d for _, d in gp.desired("shop")["dashboards"]]


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


@pytest.mark.parametrize("dashboard", _all_dashboards())
def test_dashboards_are_generated_read_only(dashboard):
    assert (dashboard["editable"], "generated" in dashboard["tags"]) == (
        False,
        True,
    )


@pytest.mark.parametrize("dashboard", _all_dashboards())
def test_dashboards_default_to_24_hours_refreshed_every_minute(dashboard):
    assert (dashboard["time"]["from"], dashboard["refresh"]) == (
        "now-24h",
        "1m",
    )


@pytest.mark.parametrize("dashboard", _all_dashboards())
def test_every_panel_names_a_datasource(dashboard):
    missing = [
        p["title"]
        for p in dashboard["panels"]
        if p["type"] != "row" and not p.get("datasource")
    ]
    assert missing == []


@pytest.mark.parametrize("dashboard", _all_dashboards())
def test_panel_ids_are_unique_within_a_dashboard(dashboard):
    ids = [p["id"] for p in dashboard["panels"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("dashboard", _all_dashboards())
def test_dashboards_survive_a_json_round_trip(dashboard):
    assert json.loads(json.dumps(dashboard)) == dashboard


def test_dashboard_uids_are_unique():
    uids = [d["uid"] for d in _all_dashboards()]
    assert len(uids) == len(set(uids))


def test_every_dashboard_lands_in_a_declared_folder():
    folders = {uid for uid, _ in gd.FOLDERS} | {""}
    placed = {folder for folder, _ in gp.desired("shop")["dashboards"]}
    assert placed <= folders


@pytest.mark.parametrize("dashboard", _all_dashboards())
def test_dashboards_never_name_a_project_or_a_tenant(dashboard):
    text = json.dumps(dashboard)

    assert ("cnp_project=" not in text, "AccountID" not in text) == (
        True,
        True,
    )


def test_the_app_dashboard_selects_an_app_and_an_environment():
    names = [v["name"] for v in gd.app_dashboard()["templating"]["list"]]
    assert {"app", "env"} <= set(names)


def test_only_project_visible_series_are_queried():
    raw = ("envoy_", "hubble_", "opencost_", "node_")
    exprs = [
        t.get("expr", "")
        for d in _all_dashboards()
        for node in _walk(d["panels"])
        for t in node.get("targets", [])
        if isinstance(t, dict)
    ]
    assert [e for e in exprs if any(r in e for r in raw)] == []


def _exprs(dashboard: dict) -> list[str]:
    return [
        t.get("expr", "")
        for node in _walk(dashboard["panels"])
        for t in node.get("targets", [])
        if isinstance(t, dict)
    ]


@pytest.mark.parametrize("build", [gd.home_dashboard, gd.app_dashboard])
def test_budget_panels_read_the_latest_sample_within_the_lookback(build):
    budget_reads = [
        e for e in _exprs(build()) if "error_budget_remaining" in e
    ]

    assert budget_reads and all(
        "last_over_time(slo:period_error_budget_remaining:ratio" in e
        and f"[{BUDGET_LOOKBACK}]" in e
        for e in budget_reads
    )


def test_the_app_latency_panel_shows_p50_p95_and_p99():
    panel = next(
        p
        for p in _walk(gd.app_dashboard()["panels"])
        if p.get("title") == "Latency"
    )

    assert [t["legendFormat"] for t in panel["targets"]] == [
        "p50",
        "p95",
        "p99",
    ]


def test_the_project_health_home_shows_p95_latency_by_app():
    exprs = _exprs(gd.home_dashboard())

    assert any("cnp_slo:latency_seconds:p95_5m" in e for e in exprs)
