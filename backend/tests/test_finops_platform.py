import io
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus
from app.models.finops import CostLineItem
from app.routers import finops as router
from app.routers import finops_finance
from app.services.finops import anomalies, finance
from app.services.finops import forecast as forecast_module
from app.services.finops import platform
from app.services.finops import provider as provider_module
from app.services.finops.engine import AppSpec, Quota
from app.services.finops.forecast import forecast_for_apps
from app.services.finops.provider import RealCostProvider
from app.services.keycloak_service import get_current_user

REF = date(2026, 11, 10)
ADMIN = {"preferred_username": "root"}
MEMBER = {"preferred_username": "mem"}
SLOTS = 5


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
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_PLATFORM_APP_SLOTS", SLOTS)
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "real")
    monkeypatch.setattr(settings, "FINOPS_ADMIN_USERS", "root")
    monkeypatch.setattr(settings, "FINOPS_FINANCE_USERS", "")
    monkeypatch.setattr(router, "_user_project_names", lambda token: ["qcm"])
    monkeypatch.setattr(
        router, "assert_project_role", lambda *_args, **_kw: None
    )
    today = SimpleNamespace(
        today=lambda: REF, fromisoformat=date.fromisoformat
    )
    monkeypatch.setattr(provider_module, "date", today)
    monkeypatch.setattr(forecast_module, "date", today)
    monkeypatch.setattr(
        router, "period_to_range", lambda p: (date(2026, 11, 1), REF)
    )


def _spec(namespace="qcm-api", project="qcm", app_id=1):
    return AppSpec(
        id=app_id,
        name=namespace,
        project_id=project,
        created_at=datetime(2026, 1, 1),
        quota=Quota(1, 1, 1, 1, 1),
        namespace=namespace,
    )


def _add(db, day, namespace, project, cost, service="Kubernetes CPU"):
    db.add(
        CostLineItem(
            x_source="opencost",
            provider_name="CNP on-prem",
            service_name=service,
            service_category="Compute",
            charge_period_start=datetime(2026, 11, day),
            charge_period_end=datetime(2026, 11, day, 23, 59),
            billing_period="2026-11",
            resource_name=namespace,
            resource_id=f"{namespace}/{service}/{day}",
            x_cost_eur=cost,
            project=project,
            app=namespace,
            cloud="onprem",
        )
    )


def _seed(db, days=(1, 2, 3)):
    """Per day: pool 10 (monitoring 8 + idle 2), qcm-api 1.0, other-ns 3.0."""
    for day in days:
        _add(db, day, "monitoring", "platform", 8.0)
        _add(db, day, "__idle__", "__idle__", 2.0)
        _add(db, day, "qcm-api", "qcm", 1.0)
        _add(db, day, "other-ns", "other", 3.0 if day != 3 else 0.0)


@pytest.fixture
def seeded(factory):
    with factory() as db:
        _seed(db)
        db.commit()
    return factory


def _cluster_total(factory, start=1, end=31):
    with factory() as db:
        return sum(
            line.x_cost_eur
            for line in db.query(CostLineItem)
            if line.x_source == "opencost"
            and start <= line.charge_period_start.day <= end
        )


def test_app_price_is_direct_cost_plus_fixed_fee_per_active_day(seeded):
    provider = RealCostProvider(seeded)

    summary = provider.summary([_spec()], REF)

    assert summary["month_to_date_eur"] == 3 * 1.0 + 3 * (10 / SLOTS)
    assert summary["breakdown"]["platform"] == 6.0


def test_fee_does_not_depend_on_the_number_of_active_apps(seeded):
    provider = RealCostProvider(seeded)

    first_day = provider.timeline([_spec()], date(2026, 11, 1), REF)[0]
    third_day = provider.timeline([_spec()], date(2026, 11, 1), REF)[2]

    assert first_day["platform"] == third_day["platform"] == 2.0


def test_app_without_cost_that_day_is_not_active_and_pays_no_fee(seeded):
    provider = RealCostProvider(seeded)

    points = provider.timeline(
        [_spec("other-ns", "other", 2)], date(2026, 11, 1), REF
    )

    assert [p["platform"] for p in points] == [2.0, 2.0, 0.0]


def test_platform_and_idle_namespaces_are_never_active_apps():
    day = platform.cluster_days(
        [
            (REF, "monitoring", "platform", 8.0),
            (REF, "__idle__", "__idle__", 2.0),
            (REF, "qcm-api", "qcm", 1.0),
        ]
    )[REF]

    assert list(day.active) == ["qcm-api"]


def test_prices_plus_uncovered_equal_the_cluster_cost(seeded):
    provider = RealCostProvider(seeded)

    specs = provider.specs_all([], REF)
    summary = provider.summary(specs, REF)

    assert summary["month_to_date_eur"] == _cluster_total(seeded)


def test_prices_plus_uncovered_equal_the_cluster_cost_every_day(seeded):
    provider = RealCostProvider(seeded)

    points = provider.timeline(
        provider.specs_all([], REF), date(2026, 11, 1), REF
    )

    assert [p["total"] for p in points] == [
        _cluster_total(seeded, d, d) for d in (1, 2, 3)
    ]


def test_specs_all_replaces_platform_and_idle_by_one_uncovered_row(seeded):
    specs = RealCostProvider(seeded).specs_all([], REF)

    assert sorted(s.namespace for s in specs) == [
        platform.UNCOVERED_NAMESPACE,
        "other-ns",
        "qcm-api",
    ]


def test_app_row_exposes_the_fee_it_includes(seeded):
    [row] = RealCostProvider(seeded).app_rows([_spec()], REF)

    assert (row["month_to_date_eur"], row["platform_eur"]) == (9.0, 6.0)


def test_uncovered_row_is_the_pool_minus_the_fees_collected(seeded):
    provider = RealCostProvider(seeded)
    row = next(
        r
        for r in provider.app_rows(provider.specs_all([], REF), REF)
        if r["name"] == platform.UNCOVERED_NAME
    )

    assert row["month_to_date_eur"] == (10 - 4) + (10 - 4) + (10 - 2)
    assert row["app_id"] < 0
    assert row["project_id"] == "platform"


def test_uncovered_is_negative_when_more_apps_run_than_slots(
    factory, monkeypatch
):
    monkeypatch.setattr(settings, "FINOPS_PLATFORM_APP_SLOTS", 1)
    with factory() as db:
        _seed(db, days=(1,))
        db.commit()
    provider = RealCostProvider(factory)

    report = provider.platform_report(date(2026, 11, 1), REF)

    assert report["uncovered_eur"] == 10 - 2 * 10


def test_platform_report_splits_total_recovered_and_uncovered(seeded):
    report = RealCostProvider(seeded).platform_report(date(2026, 11, 1), REF)

    assert (
        report["total_eur"],
        report["idle_eur"],
        report["recovered_eur"],
        report["uncovered_eur"],
        report["active_apps_avg"],
        report["fee_per_app_month_eur"],
    ) == (30.0, 6.0, 10.0, 20.0, 1.67, 2.0 * 30)
    assert report["by_namespace"] == [{"namespace": "monitoring", "eur": 24.0}]


def test_forecast_includes_the_platform_fee(factory):
    with factory() as db:
        _seed(db, days=range(1, 10))
        db.commit()

    forecast = forecast_for_apps(RealCostProvider(factory), [_spec()], REF)

    assert forecast["projected_eur"] == 30 * (1.0 + 2.0)


def test_anomalies_ignore_the_platform_fee(factory):
    with factory() as db:
        _seed(db, days=range(1, 10))
        _add(db, 10, "monitoring", "platform", 500.0)
        _add(db, 10, "qcm-api", "qcm", 1.0, service="Kubernetes memory")
        db.commit()
    provider = RealCostProvider(factory)

    daily = anomalies.app_daily(
        provider, _spec(), date(2026, 11, 1), date(2026, 11, 10)
    )

    assert set(daily.values()) == {1.0}


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def api(seeded, monkeypatch):
    monkeypatch.setattr(
        router, "get_cost_provider", lambda: RealCostProvider(seeded)
    )
    who = {"token": MEMBER}
    with seeded() as db:
        for app_id, name, project in ((1, "api", "qcm"), (2, "ns", "other")):
            namespace = "qcm-api" if project == "qcm" else "other-ns"
            db.add(
                Deployment(
                    id=app_id,
                    name=name,
                    template_id="fullstack",
                    status=DeploymentStatus.RUNNING,
                    project_id=project,
                    k8s_namespace=namespace,
                    argocd_app_name=namespace,
                )
            )
        db.commit()

    def _db():
        with seeded() as db:
            yield db

    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    app.include_router(finops_finance.router, prefix="/api")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: who["token"]
    client = TestClient(app)

    def as_user(token):
        who["token"] = token
        return client

    return as_user


def test_member_cannot_read_the_platform_report(api):
    response = api(MEMBER).get("/api/finops/platform?period=2026-11")

    assert response.status_code == 403


def test_admin_reads_the_platform_report(api):
    response = api(ADMIN).get("/api/finops/platform?period=2026-11")

    assert response.status_code == 200
    assert response.json()["uncovered_eur"] == 20.0


def test_platform_report_rejects_an_unknown_period(api):
    response = api(ADMIN).get("/api/finops/platform?period=7d")

    assert response.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "/api/finops/overview",
        "/api/finops/breakdown",
        "/api/finops/apps",
        "/api/finops/timeline",
        "/api/finops/forecast",
    ],
)
def test_member_views_never_contain_platform_namespaces_or_idle(api, url):
    response = api(MEMBER).get(url)

    body = response.text.lower()
    assert response.status_code == 200
    assert not any(
        word in body
        for word in ("monitoring", "__idle__", "inoccup", "non couverte")
    )


def test_member_sees_the_fee_in_their_app_price(api):
    response = api(MEMBER).get("/api/finops/breakdown")

    assert response.json()["platform"] == 6.0


def test_admin_overview_lists_the_uncovered_row_and_totals_the_cluster(api):
    body = api(ADMIN).get("/api/finops/overview").json()

    names = {a["name"] for a in body["apps"]}
    assert platform.UNCOVERED_NAME in names
    assert body["summary"]["month_to_date_eur"] == 39.0


def test_resource_filter_accepts_platform(api):
    points = api(MEMBER).get("/api/finops/timeline?resource=platform").json()

    assert [p["total"] for p in points] == [2.0, 2.0, 2.0]


def test_xlsx_has_a_platform_sheet_and_the_fee_per_project(api):
    response = api(ADMIN).get("/api/finops/export.xlsx?period=2026-11")

    book = load_workbook(io.BytesIO(response.content))
    projects = {
        r[0]: r[6]
        for r in book["Projets"].iter_rows(min_row=2, values_only=True)
    }
    sheet = {
        r[0]: r[1] for r in book["Plateforme"].iter_rows(values_only=True)
    }
    assert projects["qcm"] == 6.0
    assert projects["other"] == 4.0
    assert sheet["Non couvert (EUR)"] == 20.0
    assert sheet["monitoring"] == 24.0


def test_finance_cluster_days_ignore_cloud_lines():
    class Line:
        x_source = "aws_cur"
        cloud = "aws"
        charge_period_start = datetime(2026, 11, 1)
        resource_name = "x"
        project = "platform"
        x_cost_eur = 5.0

    assert finance.cluster_days([Line()]) == {}


def test_project_system_namespace_counts_in_price_but_pays_no_fee():
    days = platform.cluster_days(
        [
            (date(2026, 11, 1), "monitoring", "platform", 10.0),
            (date(2026, 11, 1), "shop-api", "shop", 1.0),
            (date(2026, 11, 1), "shop-system", "shop", 0.5),
        ]
    )

    assert list(days[date(2026, 11, 1)].active) == ["shop-api"]
