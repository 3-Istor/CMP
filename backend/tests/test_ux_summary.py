"""
The app summary and the project's apps health (CMP UX rework): how an app's
kind and state are told, the order of the project list, the shared
stale-while-revalidate cache, and how many reads the stores get.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.swr_cache import SwrCache
from app.services import topology
from app.services.moves import footprint
from app.services.observability import apps_health, stores, summary

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


# ── Kind ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("template", "kinds", "expected"),
    [
        ("import-backing", {"HTTPRoute"}, "datastore"),
        ("k3s-gitops-app", {"Rollout", "HTTPRoute", "Service"}, "web"),
        ("import-service", {"StatefulSet", "Service"}, "datastore"),
        ("import-service", {"Deployment", "Service"}, "worker"),
        ("import-service", None, "worker"),
        ("k3s-gitops-app", None, "web"),
    ],
)
def test_kind_is_told_from_the_template_and_argo_resources(
    template, kinds, expected
):
    assert summary.kind_of(template, kinds) == expected


def test_resource_kinds_are_read_from_the_prod_applications_only():
    applications = {
        "shop-db-app": {"status": {"resources": [{"kind": "StatefulSet"}]}},
        "shop-db-app-stg": {"status": {"resources": [{"kind": "HTTPRoute"}]}},
    }

    kinds = summary.resource_kinds(applications, "shop", "db", ["app"])

    assert kinds == {"StatefulSet"}


# ── State ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "error_pct", "alerts", "expected"),
    [
        ("healthy", 0.5, 0, ("healthy", None)),
        ("healthy", 7.0, 0, ("degraded", "errors_5xx")),
        ("healthy", None, 2, ("degraded", "alerts_firing")),
        ("down", 0.0, 0, ("down", "crashloop")),
        ("asleep", None, 0, ("unknown", "asleep")),
        ("not_connected", None, 0, ("unknown", "crashloop")),
    ],
)
def test_status_combines_pods_errors_and_alerts(
    state, error_pct, alerts, expected
):
    assert summary.status_of(state, "crashloop", error_pct, alerts) == expected


def test_an_app_without_ready_pods_is_down_even_if_argo_says_healthy():
    assert apps_health.base_state(["healthy"], False, 0.0, 0.0) == (
        "down",
        "no_ready_replica",
    )


def test_an_app_nobody_observes_is_unknown():
    assert apps_health.base_state(None, None, None, None) == (
        "unknown",
        "not_observed",
    )


def test_apps_needing_attention_come_first_then_by_name():
    apps = [
        {"name": "a-ok", "status": "healthy"},
        {"name": "b-unknown", "status": "unknown"},
        {"name": "z-down", "status": "down"},
        {"name": "c-degraded", "status": "degraded"},
        {"name": "a-down", "status": "down"},
    ]

    order = [a["name"] for a in apps_health.sort_apps(apps)]

    assert order == ["a-down", "z-down", "c-degraded", "b-unknown", "a-ok"]


def test_a_sparkline_has_24_points_averaged_from_the_series():
    values = [1.0, 3.0, None, None] * 24

    points = apps_health.spark(values)

    assert (len(points), points[0]) == (24, 2.0)


# ── Deploys ──────────────────────────────────────────────────────────────────


def _event(version, minutes_ago, outcome, took=60, by=None):
    started = NOW - timedelta(minutes=minutes_ago)
    return {
        "version": version,
        "started_at": started,
        "finished_at": started + timedelta(seconds=took),
        "outcome": outcome,
        "by": by,
    }


def test_the_newest_succeeded_deploy_is_live_and_older_ones_replaced():
    events = [
        _event("1.3.0", 10, "aborted_auto"),
        _event("1.2.0", 60, "promoted", by="alice"),
        _event("1.1.0", 600, "deployed"),
    ]

    rows = summary.deploy_rows(events, NOW)

    assert [(r["version"], r["outcome"]) for r in rows] == [
        ("1.3.0", "failed"),
        ("1.2.0", "live"),
        ("1.1.0", "replaced"),
    ]


def test_a_replaced_deploy_was_live_until_the_next_one_started():
    events = [
        _event("1.2.0", 60, "deployed"),
        _event("1.1.0", 600, "deployed"),
    ]

    replaced = summary.deploy_rows(events, NOW)[1]

    assert replaced["live_seconds"] == 540 * 60 - 60


def test_component_events_of_one_version_make_one_deploy():
    events = [
        _event("2.0.0", 5, "deployed", took=30),
        _event("2.0.0", 6, "deployed", took=30),
        _event("1.0.0", 100, "deployed"),
    ]

    rows = summary.deploy_rows(events, NOW)

    assert [(r["version"], r["ready_seconds"]) for r in rows[:1]] == [
        ("2.0.0", 90)
    ]


# ── Cache ────────────────────────────────────────────────────────────────────


def test_a_stale_value_is_served_while_one_refresh_runs(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.core.swr_cache.time.monotonic", lambda: clock[0])
    cache = SwrCache("test", ttl=30)
    release = asyncio.Event()
    calls = []

    async def load(value):
        calls.append(value)
        if value == "new":
            await release.wait()
        return value

    async def scenario():
        await cache.get("k", lambda: load("old"))
        clock[0] = 31
        served = [await cache.get("k", lambda: load("new")) for _ in range(3)]
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return served, await cache.get("k", lambda: load("newer"))

    served, after = asyncio.run(scenario())

    assert (served, after, calls) == (["old"] * 3, "new", ["old", "new"])


def test_concurrent_misses_share_one_load():
    cache = SwrCache("test", ttl=30)
    calls = []

    async def load():
        calls.append(1)
        await asyncio.sleep(0.01)
        return 42

    async def scenario():
        return await asyncio.gather(*(cache.get("k", load) for _ in range(5)))

    assert (asyncio.run(scenario()), len(calls)) == ([42] * 5, 1)


def test_a_failed_refresh_keeps_serving_the_stale_value(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.core.swr_cache.time.monotonic", lambda: clock[0])
    cache = SwrCache("test", ttl=30)

    async def broken():
        raise RuntimeError("store down")

    async def scenario():
        await cache.get("k", lambda: asyncio.sleep(0, result="old"))
        clock[0] = 31
        first = await cache.get("k", broken)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return first, await cache.get("k", broken)

    assert asyncio.run(scenario()) == ("old", "old")


def test_a_value_past_max_stale_is_reloaded_before_answering(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.core.swr_cache.time.monotonic", lambda: clock[0])
    cache = SwrCache("test", ttl=30, max_stale=60)

    async def scenario():
        await cache.get("k", lambda: asyncio.sleep(0, result="old"))
        clock[0] = 61
        return await cache.get("k", lambda: asyncio.sleep(0, result="new"))

    assert asyncio.run(scenario()) == "new"


# ── Apps health against fake stores ──────────────────────────────────────────


class FakeStores:
    """Answers VictoriaMetrics and Alertmanager; counts what they receive."""

    def __init__(self):
        self.sent: list[httpx.Request] = []

    def client(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(self.respond)
        )

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        if request.url.path.endswith("/api/v2/alerts"):
            return httpx.Response(200, json=[])
        form = parse_qs(request.content.decode())
        query = form["query"][0]
        if request.url.path.endswith("/query"):
            return _vector(
                [
                    ({"cnp_q": "ready", "namespace": "shop-api"}, "0"),
                    ({"cnp_q": "ready", "namespace": "shop-db"}, "1"),
                    ({"cnp_q": "rps", "namespace": "shop-web"}, "2"),
                    ({"cnp_q": "errors", "namespace": "shop-web"}, "0"),
                    ({"cnp_q": "ready", "namespace": "shop-web"}, "2"),
                ]
                if "cnp_q" in query
                else []
            )
        start = int(form["start"][0])
        return _matrix(
            [({"cnp_q": "rps", "namespace": "shop-web"}, start, "2")]
        )

    def metrics_reads(self) -> int:
        return sum(1 for r in self.sent if "/api/v1/" in r.url.path)


def _vector(rows):
    return httpx.Response(
        200,
        json={
            "data": {
                "result": [
                    {"metric": m, "value": [NOW.timestamp(), v]}
                    for m, v in rows
                ]
            }
        },
    )


def _matrix(rows):
    return httpx.Response(
        200,
        json={
            "data": {
                "result": [
                    {"metric": m, "values": [[start, v]]}
                    for m, start, v in rows
                ]
            }
        },
    )


def _argo(project, app, kind, health="Healthy"):
    return {
        f"{project}-{app}-app": {
            "status": {
                "health": {"status": health},
                "resources": [{"kind": kind}],
            }
        }
    }


@pytest.fixture
def fake_project(monkeypatch):
    fake = FakeStores()
    monkeypatch.setattr(stores, "_client", fake.client)
    for name in ("OBS_VMSINGLE_URL", "ALERTMANAGER_URL"):
        monkeypatch.setattr(stores.settings, name, "http://store.test")
    monkeypatch.setattr(stores.settings, "OBS_V3_ENABLED", True)
    argo = {
        **_argo("shop", "web", "HTTPRoute"),
        **_argo("shop", "db", "StatefulSet"),
        **_argo("shop", "api", "Deployment", "Degraded"),
    }
    monkeypatch.setattr(topology, "argo_by_name", lambda project: argo)
    monkeypatch.setattr(
        topology, "asleep_by_namespace", lambda project, cloud: {}
    )
    return fake


def _project(*names):
    return apps_health.ProjectInput(
        project="shop",
        cloud="onprem",
        apps=[
            apps_health.AppRow(
                id=i,
                name=name,
                template_id="import-service",
                components=["app"],
            )
            for i, name in enumerate(names, start=1)
        ],
    )


def test_apps_health_lists_the_down_app_first(fake_project):
    data = _project("web", "db", "api")

    found = asyncio.run(apps_health.apps_health(data, "24h", NOW))

    assert [(a["name"], a["kind"], a["status"]) for a in found["apps"]] == [
        ("api", "worker", "down"),
        ("db", "datastore", "healthy"),
        ("web", "web", "healthy"),
    ]


def test_apps_health_reads_metrics_twice_whatever_the_number_of_apps(
    fake_project,
):
    data = _project(*(f"app-{i}" for i in range(12)))

    asyncio.run(apps_health.apps_health(data, "24h", NOW))

    assert fake_project.metrics_reads() == 2


def test_apps_health_pins_every_query_to_the_projects_namespaces(
    fake_project,
):
    asyncio.run(apps_health.apps_health(_project("web", "db"), "24h", NOW))

    filters = {
        f
        for r in fake_project.sent
        if "/api/v1/" in r.url.path
        for f in parse_qs(r.content.decode())["extra_filters[]"]
    }
    assert any('namespace=~"(shop-db|shop-web)"' in f for f in filters)


def test_apps_health_gives_web_apps_an_rps_series(fake_project):
    found = asyncio.run(apps_health.apps_health(_project("web"), "24h", NOW))

    assert set(found["series"]["rps_by_app"]) == {"web"}


# ── Summary with one store down ──────────────────────────────────────────────


def test_a_summary_survives_a_store_that_does_not_answer(monkeypatch):
    async def down(*_args, **_kwargs):
        raise stores.StoreError("metrics: down")

    async def no_flows(*_args, **_kwargs):
        return False, {}

    async def no_drops(*_args, **_kwargs):
        return 0.0

    monkeypatch.setattr(stores, "instant", down)
    monkeypatch.setattr(stores, "series", down)
    monkeypatch.setattr(
        stores,
        "capabilities",
        lambda: {"metrics": True, "logs": False, "alerts": False},
    )
    monkeypatch.setattr(topology, "argo_by_name", lambda p: {})
    monkeypatch.setattr(topology, "observed_flows", no_flows)
    monkeypatch.setattr(topology, "denied_flows", no_drops)
    monkeypatch.setattr(summary, "_last_backup", lambda ns, cloud: None)
    health = SimpleNamespace(
        status="healthy",
        components=[
            SimpleNamespace(state="healthy", reason=None, ready=2, desired=2)
        ],
    )
    monkeypatch.setattr(summary, "_read_health", lambda i, now: health)
    data = summary.AppInput(
        deployment_id=1,
        scope=stores.Scope("shop", "web", "shop-web", "onprem"),
        template_id="k3s-gitops-app",
        components=["app"],
        project_apps=["web"],
        events=[_event("1.0.0", 30, "deployed")],
    )

    found = asyncio.run(summary.app_summary(data, "1h", NOW))

    assert (
        found["status"],
        found["tiles"]["replicas_ready"],
        found["deploys"][0]["outcome"],
    ) == ("healthy", 2, "live")


# ── Footprint usage ──────────────────────────────────────────────────────────


def test_live_usage_sums_prod_and_staging_like_the_requests(monkeypatch):
    rows = [
        ({"cnp_q": "cpu_cores", "namespace": "shop-web"}, "0.2"),
        ({"cnp_q": "cpu_cores", "namespace": "shop-web-stg"}, "0.05"),
        ({"cnp_q": "mem_bytes", "namespace": "shop-web"}, "100"),
        ({"cnp_q": "mem_bytes", "namespace": "other-web"}, "999"),
    ]
    monkeypatch.setattr(
        stores,
        "_client",
        lambda base_url: httpx.AsyncClient(
            base_url=base_url,
            transport=httpx.MockTransport(lambda r: _vector(rows)),
        ),
    )
    monkeypatch.setattr(stores.settings, "OBS_VMSINGLE_URL", "http://vm.test")

    usage = asyncio.run(footprint.live_usage("shop", "onprem", {"web"}, NOW))

    assert usage == {"web": {"cpu_cores": 0.25, "mem_bytes": 100.0}}
