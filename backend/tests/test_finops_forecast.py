import asyncio
import random
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus
from app.models.finops import CostAlert, CostLineItem, ProjectBudget
from app.routers import finops as router
from app.services.finops import alert_poller
from app.services.finops import forecast as forecast_module
from app.services.finops import provider as provider_module
from app.services.finops import recommendations_real as recs
from app.services.finops.forecast import forecast_month
from app.services.finops.provider import RealCostProvider

REF = date(2026, 11, 15)  # 14 days done, 16 left (Nov 15..30)


def _history(value_of, days=28, ref=REF):
    return {
        ref - timedelta(days=i): value_of(ref - timedelta(days=i))
        for i in range(1, days + 1)
    }


def _weekly(day):
    return 1.0 if day.weekday() >= 5 else 3.0


# ── forecast math ────────────────────────────────────────────────────────────


def test_constant_series_projects_actual_plus_remaining_days():
    daily = _history(lambda d: 1.0)

    result = forecast_month(daily, REF)

    assert (result["actual_eur"], result["projected_eur"]) == (14.0, 30.0)


def test_constant_series_has_no_spread():
    result = forecast_month(_history(lambda d: 1.0), REF)

    assert result["low_eur"] == result["high_eur"] == result["projected_eur"]


def test_weekly_pattern_is_forecast_per_weekday():
    daily = _history(_weekly)
    remaining = [REF + timedelta(days=i) for i in range(16)]

    result = forecast_month(daily, REF)

    assert [p["eur"] for p in result["daily"]] == [
        _weekly(d) for d in remaining
    ]


def test_weekly_pattern_month_total_is_exact():
    daily = _history(_weekly)
    month = [date(2026, 11, d) for d in range(1, 31)]

    result = forecast_month(daily, REF)

    assert result["method"] == "weekday_mean"
    assert result["projected_eur"] == sum(_weekly(d) for d in month)


def test_short_history_uses_plain_mean_and_flags_low_history():
    daily = _history(lambda d: 2.0, days=3)

    result = forecast_month(daily, REF)

    assert (result["method"], result["low_history"]) == ("mean", True)


def test_short_history_gets_a_band_even_when_days_are_identical():
    result = forecast_month(_history(lambda d: 2.0, days=3), REF)

    assert result["high_eur"] - result["low_eur"] > 0


def test_shorter_history_gives_a_wider_interval():
    rng = random.Random(7)
    noisy = _history(lambda d: 2.0 + rng.gauss(0, 0.2))
    long = forecast_month(noisy, REF)
    short = forecast_month(
        {d: v for d, v in noisy.items() if d >= REF - timedelta(days=5)}, REF
    )

    assert (short["high_eur"] - short["low_eur"]) > (
        long["high_eur"] - long["low_eur"]
    )


def test_no_history_projects_month_to_date():
    result = forecast_month({}, REF)

    assert (result["method"], result["projected_eur"]) == ("none", 0.0)


def test_ingest_gaps_are_not_counted_as_free_days():
    daily = _history(lambda d: 1.0)
    del daily[REF - timedelta(days=3)]
    del daily[REF - timedelta(days=10)]

    result = forecast_month(daily, REF)

    assert result["daily"][0]["eur"] == 1.0


def test_today_partial_data_is_not_used_as_history():
    daily = _history(lambda d: 1.0)
    daily[REF] = 0.1

    assert forecast_month(daily, REF)["projected_eur"] == 30.0


def test_eighty_percent_interval_covers_about_eighty_percent_of_outcomes():
    rng = random.Random(42)
    month_end = date(2026, 11, 30)
    hits = 0
    trials = 400
    for _ in range(trials):
        truth = {
            d: _weekly(d) + rng.gauss(0, 0.4)
            for d in (
                REF - timedelta(days=28) + timedelta(days=i)
                for i in range((month_end - REF).days + 29)
            )
        }
        past = {d: v for d, v in truth.items() if d < REF}
        result = forecast_month(past, REF)
        total = sum(v for d, v in truth.items() if d.month == 11)
        hits += result["low_eur"] <= total <= result["high_eur"]

    assert 0.7 <= hits / trials <= 0.9


# ── overrun date ─────────────────────────────────────────────────────────────


def test_overrun_date_is_first_day_cumulative_forecast_exceeds_budget():
    result = forecast_month(_history(lambda d: 1.0), REF, budget_eur=20.0)

    assert result["overrun_date"] == date(2026, 11, 21)


def test_no_overrun_date_when_forecast_stays_under_budget():
    result = forecast_month(_history(lambda d: 1.0), REF, budget_eur=31.0)

    assert result["overrun_date"] is None


def test_overrun_date_in_the_past_when_budget_is_already_spent():
    result = forecast_month(_history(lambda d: 1.0), REF, budget_eur=5.0)

    assert result["overrun_date"] == date(2026, 11, 6)


# ── provider, API and poller ─────────────────────────────────────────────────


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _line(day: date, cost: float, namespace="qcm-api", project="qcm"):
    return CostLineItem(
        x_source="opencost",
        provider_name="CNP on-prem",
        service_name="Kubernetes CPU",
        service_category="Compute",
        charge_period_start=datetime.combine(day, datetime.min.time()),
        charge_period_end=datetime.combine(day, datetime.max.time()),
        billing_period=f"{day:%Y-%m}",
        resource_id=f"{namespace}/{day}",
        resource_name=namespace,
        x_cost_eur=cost,
        project=project,
        cloud="onprem",
    )


@pytest.fixture
def seeded(factory):
    with factory() as db:
        db.add(
            Deployment(
                id=1,
                name="api",
                template_id="fullstack",
                status=DeploymentStatus.RUNNING,
                project_id="qcm",
                k8s_namespace="qcm-api",
            )
        )
        db.add(
            Deployment(
                id=2,
                name="web",
                template_id="static",
                status=DeploymentStatus.RUNNING,
                project_id="other",
                k8s_namespace="other-web",
            )
        )
        for day in _history(lambda d: 1.0):
            db.add(_line(day, 1.0))
            db.add(_line(day, 5.0, "other-web", "other"))
        db.add(ProjectBudget(project_name="qcm", monthly_amount_eur=20.0))
        db.commit()
    return factory


@pytest.fixture
def real_mode(monkeypatch, factory):
    today = SimpleNamespace(
        today=lambda: REF, fromisoformat=date.fromisoformat
    )
    recs._cache.clear()
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "real")
    monkeypatch.setattr(provider_module, "date", today)
    monkeypatch.setattr(forecast_module, "date", today)
    monkeypatch.setattr(
        provider_module, "SessionLocal", factory, raising=False
    )
    monkeypatch.setattr(
        router, "get_cost_provider", lambda: RealCostProvider(factory)
    )
    monkeypatch.setattr(
        router, "period_to_range", lambda p: (REF - timedelta(days=29), REF)
    )
    monkeypatch.setattr(
        recs.httpx,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")),
    )


def test_forecast_reads_the_cost_store_of_the_apps(seeded):
    apps = RealCostProvider(seeded).specs(
        [
            SimpleNamespace(
                id=1,
                name="api",
                project_id="qcm",
                k8s_namespace="qcm-api",
                created_at=datetime(2026, 1, 1),
                app_config=None,
            )
        ]
    )

    result = forecast_module.forecast_for_apps(
        RealCostProvider(seeded), apps, REF
    )

    assert result["projected_eur"] == 30.0


def test_overview_projection_uses_the_forecast_with_real_costs(
    seeded, real_mode, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    with seeded() as db:
        overview = router.get_overview({}, db, "qcm", "30d", "daily", None)

    assert (
        overview.summary.projected_month_eur == overview.forecast.projected_eur
    )
    assert overview.forecast.overrun_date == date(2026, 11, 21)


def test_overview_has_no_forecast_with_simulated_costs(seeded, monkeypatch):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    with seeded() as db:
        overview = router.get_overview({}, db, "qcm", "30d", "daily", None)

    assert overview.forecast is None


def test_forecast_endpoint_carries_the_project_budget(
    seeded, real_mode, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    with seeded() as db:
        result = router.get_forecast({}, db, "qcm")

    assert (result.budget_eur, result.projected_eur) == (20.0, 30.0)


def test_forecast_endpoint_denies_a_project_the_user_has_no_role_on(
    seeded, real_mode, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: False)

    def deny(*_args):
        raise HTTPException(status_code=403)

    monkeypatch.setattr(router, "assert_project_role", deny)
    with seeded() as db, pytest.raises(HTTPException) as err:
        router.get_forecast({}, db, "other")

    assert err.value.status_code == 403


def test_forecast_endpoint_only_counts_projects_the_user_belongs_to(
    seeded, real_mode, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: False)
    monkeypatch.setattr(router, "_user_project_names", lambda token: ["qcm"])
    with seeded() as db:
        result = router.get_forecast({}, db, None)

    assert result.projected_eur == 30.0


@pytest.fixture
def poller(seeded, real_mode, monkeypatch):
    sent = []

    async def record(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(alert_poller, "SessionLocal", seeded)
    monkeypatch.setattr(
        alert_poller, "get_cost_provider", lambda: RealCostProvider(seeded)
    )
    monkeypatch.setattr(
        alert_poller.discord_service, "send_forecast_alert", record
    )
    return sent


def test_forecast_overrun_is_notified_once_per_month(seeded, poller):
    asyncio.run(alert_poller.check_forecasts(REF))
    asyncio.run(alert_poller.check_forecasts(REF + timedelta(days=1)))

    assert [s["overrun_date"] for s in poller] == ["21/11/2026"]


def test_forecast_overrun_is_kept_in_alert_history(seeded, poller):
    asyncio.run(alert_poller.check_forecasts(REF))

    with seeded() as db:
        alert = db.scalars(
            select(CostAlert).where(CostAlert.kind == "forecast")
        ).one()
    assert alert.message.startswith("Dépassement prévu le 21/11/2026")


def test_no_forecast_alert_with_simulated_costs(seeded, poller, monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "simulated")

    asyncio.run(alert_poller.check_forecasts(REF))

    assert poller == []
