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


def _vm(monkeypatch, series):
    def fake_get(url, params, timeout):
        assert url.endswith("/api/v1/query")
        query = params["query"]
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
):
    ns = "qcm-api"
    return [
        ('resource="cpu"', {ns: cpu_request}),
        ('resource="memory"', {ns: 4 * 2**30}),
        ("count by", {ns: 1}),
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
