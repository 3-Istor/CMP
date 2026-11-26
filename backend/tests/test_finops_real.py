import asyncio
import csv
import io
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.finops import CostLineItem
from app.routers import finops as router
from app.services.environments.observe import CONTAINER
from app.services.finops import engine
from app.services.finops import provider as provider_module
from app.services.finops import recommendations_real as recs
from app.services.finops.engine import AppSpec, Quota
from app.services.finops.provider import (
    RealCostProvider,
    SimulatedCostProvider,
    get_cost_provider,
)

FIXTURES = Path(__file__).parent / "fixtures" / "finops"
QUOTA = Quota(1, 1, 1, 1, 1)
REF = date(2026, 11, 10)


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_DATA_DIR", str(FIXTURES))
    recs._cache.clear()


def _app(app_id=1, namespace="qcm-api", project="qcm"):
    return AppSpec(
        id=app_id,
        name=namespace,
        project_id=project,
        created_at=datetime(2026, 1, 1),
        quota=QUOTA,
        namespace=namespace,
    )


def _add(db, day, cost, service="Kubernetes CPU", **overrides):
    values = {
        "x_source": "opencost",
        "provider_name": "CNP on-prem",
        "service_name": service,
        "service_category": "Compute",
        "charge_period_start": datetime(2026, 11, day),
        "charge_period_end": datetime(2026, 11, day, 23, 59),
        "billing_period": "2026-11",
        "resource_name": "qcm-api",
        "x_cost_eur": cost,
        "project": "qcm",
        "app": "api",
        "cloud": "onprem",
    }
    values.update(overrides)
    values.setdefault(
        "resource_id", f"{values['resource_name']}/{service}/{day}"
    )
    db.add(CostLineItem(**values))


@pytest.fixture
def seeded(factory):
    with factory() as db:
        _add(db, 1, 1.0)
        _add(db, 1, 0.5, "Kubernetes memory")
        _add(db, 2, 2.0)
        _add(db, 2, 1.0, "Persistent volumes", service_category="Storage")
        _add(
            db,
            2,
            0.25,
            "gpt",
            x_source="ai_gateway",
            service_category="AI and Machine Learning",
            resource_name="qcm/agent/gpt",
        )
        _add(db, 2, 9.0, resource_name="other-ns", project="other")
        _add(db, 3, 3.0, x_is_final=True)
        db.commit()
    return factory


def test_summary_sums_month_to_date_and_projects_from_days_with_data(seeded):
    provider = RealCostProvider(seeded)

    summary = provider.summary([_app()], REF)

    assert summary["month_to_date_eur"] == 7.75
    assert summary["projected_month_eur"] == round(7.75 / 3 * 30, 2)


def test_summary_ignores_namespaces_of_other_apps(seeded):
    provider = RealCostProvider(seeded)

    assert provider.summary([_app()], REF)["month_to_date_eur"] < 9.0


def test_breakdown_maps_services_to_resource_keys(seeded):
    provider = RealCostProvider(seeded)

    breakdown = provider.breakdown([_app()], REF)

    assert breakdown == {
        "cpu": 6.0,
        "ram": 0.5,
        "storage": 1.0,
        "network": 0.0,
        "ai": 0.25,
        "platform": 0.0,
    }


def test_timeline_returns_daily_points_and_weekly_buckets(seeded):
    provider = RealCostProvider(seeded)

    daily = provider.timeline([_app()], date(2026, 11, 1), REF, "daily")
    weekly = provider.timeline([_app()], date(2026, 11, 1), REF, "weekly")

    assert [p["total"] for p in daily] == [1.5, 3.25, 3.0]
    assert [(p["date"], p["total"]) for p in weekly] == [
        ("2026-10-26", 1.5),
        ("2026-11-02", 6.25),
    ]


def test_previous_month_and_trend_compare_with_last_month(factory):
    with factory() as db:
        _add(
            db,
            1,
            10.0,
            charge_period_start=datetime(2026, 10, 5),
            billing_period="2026-10",
        )
        _add(db, 1, 5.0)
        db.commit()

    summary = RealCostProvider(factory).summary([_app()], REF)

    assert (summary["previous_month_eur"], summary["trend_pct"]) == (
        10.0,
        -50.0,
    )


def test_app_rows_report_latest_day_and_trend(seeded):
    provider = RealCostProvider(seeded)

    rows = provider.app_rows([_app()], REF)

    assert (rows[0]["cost_per_day_eur"], rows[0]["trend_pct"]) == (
        3.0,
        -7.7,
    )


def test_by_cloud_splits_month_to_date_per_cloud(seeded):
    with seeded() as db:
        _add(db, 2, 4.0, cloud="aws", x_source="aws_cur")
        db.commit()

    split = RealCostProvider(seeded).by_cloud([_app()], REF)

    assert split == {"onprem": 7.75, "aws": 4.0, "gcp": 0.0}


def test_get_cost_provider_follows_setting(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "real")
    real = get_cost_provider()
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "simulated")

    assert isinstance(real, RealCostProvider)
    assert isinstance(get_cost_provider(), SimulatedCostProvider)


# ── recommendations ──────────────────────────────────────────────────────────


def _vm(monkeypatch, series, cluster="onprem", usd_per_eur=None):
    monkeypatch.setattr(recs, "_usd_per_eur", lambda: usd_per_eur)
    wanted = recs.metrics_isolation.cluster_filter(cluster)

    def fake_get(url, params, timeout):
        assert url.endswith("/api/v1/query")
        query = params["query"]
        if params["extra_filters[]"] != wanted:
            return httpx.Response(
                200,
                json={"data": {"result": []}},
                request=httpx.Request("GET", url),
            )
        for needle, values in series:
            if needle in query:
                result = [
                    {"metric": {"namespace": ns}, "value": [0, str(v)]}
                    for ns, v in values.items()
                ]
                return httpx.Response(
                    200,
                    json={"data": {"result": result}},
                    request=httpx.Request("GET", url),
                )
        raise AssertionError(query)

    monkeypatch.setattr(recs.httpx, "get", fake_get)


def _usage(
    cpu_p95=0.4,
    history=14,
    network=5e9,
    replicas=4,
    cpu_request=4.0,
    deployments=1,
    app=None,
):
    ns = "qcm-api"
    # Per pod, app container only: request, limit, p95 CPU, p99 and peak
    # memory (GiB).
    a = {
        "cpu_request": cpu_request,
        "ram_request": 4,
        "cpu_limit": None,
        "ram_limit": None,
        "cpu_p95": cpu_p95,
        "ram_p99": 3.5,
        "ram_peak": 3.5,
    }
    a.update(app or {})
    gib = 2**30
    sel = f'namespace=~"{ns}",container="{CONTAINER}"'

    def series(needle, value, unit=1):
        return (needle, {} if value is None else {ns: value * unit})

    return [
        series(f'requests{{{sel},resource="cpu"', a["cpu_request"]),
        series(f'requests{{{sel},resource="memory"', a["ram_request"], gib),
        series(f'limits{{{sel},resource="cpu"', a["cpu_limit"]),
        series(f'limits{{{sel},resource="memory"', a["ram_limit"], gib),
        series(
            f"rate(container_cpu_usage_seconds_total{{{sel}}}", a["cpu_p95"]
        ),
        series(
            f"(container_memory_working_set_bytes{{{sel}}})[",
            a["ram_p99"],
            gib,
        ),
        series(
            f"max_over_time(container_memory_working_set_bytes{{{sel}}}",
            a["ram_peak"],
            gib,
        ),
        ('resource="cpu"', {ns: cpu_request}),
        ('resource="memory"', {ns: 4 * 2**30}),
        ("count by", {ns: deployments}),
        ("kube_deployment_spec_replicas", {ns: replicas}),
        ("quantile_over_time(0.95", {ns: cpu_p95}),
        ("quantile_over_time(0.99", {ns: 3.5 * 2**30}),
        ("count_over_time", {ns: history}),
        ("container_network_receive_bytes_total", {ns: network}),
    ]


def test_recommends_fewer_replicas_when_p95_cpu_is_far_below_requests(
    monkeypatch,
):
    _vm(monkeypatch, _usage(cpu_p95=0.4))

    result = recs.recommendations_for_apps([_app()], REF)

    assert [(r.id, r.recommended) for r in result] == [
        ("1:replicas", {"replicas": 1})
    ]
    assert result[0].monthly_saving_eur > 0


def test_recommends_rightsizing_for_single_replica_with_oversized_requests(
    monkeypatch,
):
    _vm(monkeypatch, _usage(cpu_p95=0.4, replicas=1))

    result = recs.recommendations_for_apps([_app()], REF)

    assert result[0].id == "1:rightsizing"
    assert result[0].recommended["cpu_cores"] == 0.52


_PER_POD = {
    "cpu_request": 0.5,
    "ram_request": 1,
    "cpu_limit": 1,
    "ram_limit": 2,
    "cpu_p95": 0.1,
    "ram_p99": 0.25,
    "ram_peak": 0.3,
}


def test_rightsizing_patch_sets_requests_and_limits_per_pod(monkeypatch):
    _vm(monkeypatch, _usage(replicas=1, app=_PER_POD))

    rec = recs.recommendations_for_apps([_app()], REF)[0]

    assert rec.id == "1:rightsizing"
    assert rec.patch == {
        "resources": {
            "requests": {"cpu": "130m", "memory": "308Mi"},
            "limits": {"cpu": "260m", "memory": "615Mi"},
        }
    }
    assert rec.current == {"cpu_cores": 0.5, "ram_gb": 1.0}


def test_rightsizing_memory_limit_covers_the_peak_when_request_equals_limit(
    monkeypatch,
):
    # The static template sets the memory request equal to its limit.
    app = {**_PER_POD, "ram_limit": 1, "ram_peak": 0.6}
    _vm(monkeypatch, _usage(replicas=1, app=app))

    rec = recs.recommendations_for_apps([_app()], REF)[0]

    assert rec.patch["resources"]["requests"]["memory"] == "308Mi"
    assert rec.patch["resources"]["limits"]["memory"] == "738Mi"


def test_hibernation_is_not_applicable(monkeypatch):
    # The chart renders `replicaCount | default 1`: 0 would keep one pod.
    _vm(monkeypatch, _usage(network=10))

    rec = recs.recommendations_for_apps([_app()], REF)[0]

    assert rec.id == "1:inactivity"
    assert rec.patch is None


def test_rightsizing_has_no_patch_before_a_full_history(monkeypatch):
    _vm(monkeypatch, _usage(replicas=1, history=2, app=_PER_POD))

    rec = recs.recommendations_for_apps([_app()], REF)[0]

    assert rec.id == "1:rightsizing"
    assert rec.patch is None


def test_rightsizing_has_no_patch_when_the_namespace_runs_several_deployments(
    monkeypatch,
):
    _vm(monkeypatch, _usage(replicas=1, deployments=2, app=_PER_POD))

    rec = recs.recommendations_for_apps([_app()], REF)[0]

    assert rec.id == "1:rightsizing"
    assert rec.patch is None


def test_rightsizing_omits_a_limit_the_pod_does_not_set(monkeypatch):
    app = {**_PER_POD, "cpu_limit": None, "ram_limit": None}
    _vm(monkeypatch, _usage(replicas=1, app=app))

    rec = recs.recommendations_for_apps([_app()], REF)[0]

    assert list(rec.patch["resources"]) == ["requests"]


def test_recommends_hibernation_when_namespace_received_no_traffic(
    monkeypatch,
):
    _vm(monkeypatch, _usage(network=10))

    result = recs.recommendations_for_apps([_app()], REF)

    assert [r.id for r in result] == ["1:inactivity"]


def test_confidence_drops_when_history_is_shorter_than_14_days(monkeypatch):
    _vm(monkeypatch, _usage(history=14))
    full = recs.recommendations_for_apps([_app()], REF)[0].confidence
    recs._cache.clear()
    _vm(monkeypatch, _usage(history=3))

    short = recs.recommendations_for_apps([_app()], REF)[0].confidence

    assert short < full


def test_saving_is_priced_with_tco_unit_costs(monkeypatch):
    _vm(monkeypatch, _usage(cpu_p95=0.4, replicas=1))
    rec = recs.recommendations_for_apps([_app()], REF)[0]
    tco = recs.inputs.onprem_tco()

    expected = (4.0 - 0.52) * tco.cpu_eur_per_core_hour * 730

    assert rec.monthly_saving_eur == pytest.approx(expected, abs=0.01)


def test_cloud_advice_uses_the_cloud_price_and_its_own_id(monkeypatch):
    _vm(
        monkeypatch,
        _usage(cpu_p95=0.4, replicas=1),
        cluster="aws",
        usd_per_eur=1.08,
    )

    [rec] = recs.recommendations_for_apps([_app()], REF)
    aws = recs.inputs.cluster_rates(1.08)["aws"]

    assert rec.id == "1:rightsizing:aws"
    assert rec.title.endswith("(AWS)")
    assert rec.monthly_saving_eur == pytest.approx(
        (4.0 - 0.52) * aws.cpu_eur_per_core_hour * 730, abs=0.01
    )


def test_cloud_saving_counts_only_the_share_of_time_it_ran(monkeypatch):
    _vm(
        monkeypatch,
        _usage(cpu_p95=0.4, replicas=1, history=3.5),
        cluster="aws",
        usd_per_eur=1.08,
    )

    [rec] = recs.recommendations_for_apps([_app()], REF)
    aws = recs.inputs.cluster_rates(1.08)["aws"]

    # 3.5 days of data over the 14-day window: a quarter of a month
    assert rec.monthly_saving_eur == pytest.approx(
        (4.0 - 0.52) * aws.cpu_eur_per_core_hour * 730 / 4, abs=0.01
    )


def test_clouds_get_no_advice_while_the_usd_rate_is_unknown(monkeypatch):
    _vm(monkeypatch, _usage(cpu_p95=0.4, replicas=1), cluster="aws")

    assert recs.recommendations_for_apps([_app()], REF) == []


def test_returns_nothing_when_victoriametrics_is_unreachable(monkeypatch):
    def down(*_args, **_kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(recs.httpx, "get", down)

    assert recs.recommendations_for_apps([_app()], REF) == []


def test_real_provider_does_not_fall_back_to_simulation_when_vm_is_down(
    seeded, monkeypatch
):
    monkeypatch.setattr(
        recs.httpx,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")),
    )

    summary = RealCostProvider(seeded).summary([_app()], REF)

    assert summary["potential_savings_eur"] == 0.0


# ── meta ─────────────────────────────────────────────────────────────────────


def test_meta_reports_freshness_and_estimates_per_source(seeded, monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "real")
    with seeded() as db:
        _add(db, 4, 1.0, x_is_estimate=True)
        db.commit()

        meta = router.get_meta({}, db)

    opencost = next(s for s in meta.sources if s.source == "opencost")
    assert meta.provider == "real"
    assert (
        opencost.latest_day,
        opencost.final_through,
        opencost.has_estimate,
    ) == (date(2026, 11, 4), date(2026, 11, 3), True)


def test_meta_reports_first_day_of_each_source(seeded, monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "real")
    with seeded() as db:
        meta = router.get_meta({}, db)

    opencost = next(s for s in meta.sources if s.source == "opencost")
    assert opencost.first_day is not None
    assert opencost.first_day <= opencost.latest_day


def test_meta_says_simulated_by_default(factory):
    with factory() as db:
        assert router.get_meta({}, db).provider == "simulated"


# ── CSV export ───────────────────────────────────────────────────────────────


def _export(db, token, project=None, period="30d", cloud=None):
    response = router.export_csv(token, db, project, period, cloud)

    async def collect():
        return "".join([c async for c in response.body_iterator])

    return list(csv.DictReader(io.StringIO(asyncio.run(collect()))))


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "real")
    monkeypatch.setattr(
        provider_module,
        "date",
        SimpleNamespace(today=lambda: REF, fromisoformat=date.fromisoformat),
    )
    monkeypatch.setattr(
        router, "period_to_range", lambda p: (date(2026, 11, 1), REF)
    )


def test_export_is_refused_with_409_when_costs_are_simulated(factory):
    with factory() as db, pytest.raises(HTTPException) as err:
        router.export_csv({}, db, None, "30d", None)

    assert err.value.status_code == 409


def test_export_uses_focus_column_names(seeded, real_mode, monkeypatch):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    with seeded() as db:
        rows = _export(db, {})

    assert list(rows[0]) == [name for name, _ in router._EXPORT_COLUMNS]
    assert rows[0]["x_Project"] == "qcm"
    assert rows[0]["x_IsFinal"] == "false"


def test_export_only_contains_projects_the_user_belongs_to(
    seeded, real_mode, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: False)
    monkeypatch.setattr(router, "_user_project_names", lambda token: ["qcm"])
    with seeded() as db:
        rows = _export(db, {})

    assert {r["x_Project"] for r in rows} == {"qcm"}


def test_export_lets_a_cnp_admin_see_every_project(
    seeded, real_mode, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    with seeded() as db:
        rows = _export(db, {})

    assert {r["x_Project"] for r in rows} == {"qcm", "other"}


def test_export_denies_a_project_the_user_has_no_role_on(
    seeded, real_mode, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: False)

    def deny(*_args):
        raise HTTPException(status_code=403)

    monkeypatch.setattr(router, "assert_project_role", deny)
    with seeded() as db, pytest.raises(HTTPException) as err:
        _export(db, {}, project="other")

    assert err.value.status_code == 403


def test_cloud_apps_count_their_invoice_split_not_opencost_list_prices(
    seeded,
):
    with seeded() as db:
        _add(
            db, 2, 50.0, cloud="aws", resource_name="shop-api", project="shop"
        )
        _add(
            db,
            2,
            3.0,
            cloud="aws",
            x_source="aws_split",
            resource_name="shop-api",
            project="shop",
        )
        db.commit()

    summary = RealCostProvider(seeded).summary(
        [_app(2, "shop-api", "shop")], REF
    )

    assert summary["month_to_date_eur"] == 3.0


def test_specs_all_adds_namespaces_that_are_not_cmp_apps(seeded):
    provider = RealCostProvider(seeded)

    specs = provider.specs_all([], REF)

    by_ns = {s.namespace: s for s in specs}
    assert set(by_ns) == {"qcm-api", "other-ns", "__uncovered__"}
    assert by_ns["other-ns"].project_id == "other"
    assert by_ns["other-ns"].id < 0


def test_app_spec_falls_back_to_the_project_app_namespace():
    class _Row:
        id = 7
        name = "api"
        project_id = "shop"
        k8s_namespace = None
        app_config = None
        created_at = datetime(2026, 1, 1)

    spec = engine.app_spec_from_deployment(_Row())

    assert spec.namespace == "shop-api"


def test_rightsizing_text_is_english_with_a_key_the_ui_translates(monkeypatch):
    # Arrange
    _vm(monkeypatch, _usage(replicas=1, app=_PER_POD))

    # Act
    rec = recs.recommendations_for_apps([_app()], REF)[0]

    # Assert
    assert (rec.title, rec.text_key, sorted(rec.text_params)) == (
        "Adjust the CPU / RAM requests",
        "rightsizing",
        ["cpu", "ram", "window"],
    )


def test_month_to_date_matches_the_summary_figure(seeded):
    assert RealCostProvider(seeded).month_to_date([_app()], REF) == 7.75


# ── public clouds in the overview ────────────────────────────────────────────


def _aws_month(db):
    """Day 2 on AWS: 3.0 untagged, a 4.0 node split onto shop-api, -7.0 credit."""
    invoice = {"x_source": "aws_focus", "cloud": "aws", "app": None}
    _add(db, 2, 3.0, resource_name="vpc", project=None, **invoice)
    _add(db, 2, 4.0, resource_name="i-1", project="__cluster__", **invoice)
    _add(
        db,
        2,
        -7.0,
        resource_name="credit",
        project="__credits__",
        charge_category="Credit",
        **invoice,
    )
    _add(
        db,
        2,
        4.0,
        x_source="aws_split",
        cloud="aws",
        resource_name="shop-api",
        project="shop",
        app="api",
        x_is_estimate=True,
    )


def test_by_cloud_whole_platform_counts_full_invoice(seeded):
    with seeded() as db:
        _aws_month(db)
        db.commit()
    provider = RealCostProvider(seeded)

    split = provider.by_cloud([_app()], REF, whole_platform=True)

    assert split == {"onprem": 7.75, "aws": 7.0, "gcp": 0.0}
    assert provider.credits_by_cloud(REF) == {"aws": 7.0, "gcp": 0.0}


def test_by_cloud_project_view_unchanged(seeded):
    with seeded() as db:
        _aws_month(db)
        db.commit()

    split = RealCostProvider(seeded).by_cloud([_app()], REF)

    assert split == {"onprem": 7.75, "aws": 0.0, "gcp": 0.0}


def test_specs_all_lists_cloud_split_namespaces(seeded):
    with seeded() as db:
        for namespace, project in (
            ("cnp108-aws-app-html-css-aws", "cnp108"),
            ("__idle__", "__idle__"),
        ):
            _add(
                db,
                2,
                1.0,
                x_source="aws_split",
                cloud="aws",
                resource_name=namespace,
                project=project,
            )
        db.commit()

    specs = RealCostProvider(seeded).specs_all([], REF)

    names = {s.namespace for s in specs}
    assert "cnp108-aws-app-html-css-aws" in names
    assert "__idle__" not in names


def test_app_row_flags_estimated_cost(seeded):
    with seeded() as db:
        _aws_month(db)
        db.commit()

    rows = RealCostProvider(seeded).app_rows(
        [_app(), _app(2, "shop-api", "shop")], REF
    )

    estimated = {r["name"]: r["estimated"] for r in rows}
    assert estimated == {"qcm-api": False, "shop-api": True}


@pytest.fixture
def overview(seeded, monkeypatch):
    monkeypatch.setattr(
        provider_module,
        "date",
        SimpleNamespace(today=lambda: REF, fromisoformat=date.fromisoformat),
    )
    monkeypatch.setattr(
        router, "get_cost_provider", lambda: RealCostProvider(seeded)
    )
    monkeypatch.setattr(
        router, "period_to_range", lambda p: (date(2026, 11, 1), REF)
    )
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    with seeded() as db:
        _aws_month(db)
        db.commit()

    def call(project=None, cloud=None):
        with seeded() as db:
            return router.get_overview(
                {}, db, project, "30d", "daily", cloud, None
            )

    return call


def test_overview_of_the_whole_platform_shows_credits_and_cloud_apps(
    overview,
):
    body = overview(cloud="aws")

    assert body.by_cloud["aws"] == 7.0
    assert body.credits_by_cloud == {"aws": 7.0, "gcp": 0.0}
    assert [(a.name, a.estimated) for a in body.apps] == [("shop-api", True)]


def test_overview_of_a_project_has_no_credits(overview):
    body = overview(project="qcm")

    assert body.credits_by_cloud == {}
    assert body.by_cloud["aws"] == 0.0
