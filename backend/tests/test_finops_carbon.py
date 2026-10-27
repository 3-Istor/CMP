import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.models.finops import CarbonIntensity, CostLineItem, SavingsEntry
from app.routers import finops as router
from app.services.finops import ingest
from app.services.finops.ingest import carbon, inputs, savings
from app.services.finops.ingest.store import CostLine
from app.services.keycloak_service import get_current_user

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date.today()


def _payload(name):
    return json.loads((FIXTURES / name).read_text())


def _fake_get(url, params=None, timeout=None):
    name = (
        "rte_eco2mix_cons_def_daily.json"
        if carbon.DATASET_CONSOLIDATED in url
        else "rte_eco2mix_tr_daily.json"
    )
    response = mock.Mock()
    response.json.return_value = _payload(name)
    return response


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def rte(monkeypatch):
    monkeypatch.setattr(carbon.httpx, "get", _fake_get)


@pytest.fixture
def tco(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_DATA_DIR", str(FIXTURES / "finops"))
    return inputs.onprem_tco()


def test_fetch_queries_the_odre_records_endpoint_for_the_taux_co2_field(
    monkeypatch,
):
    get = mock.Mock(side_effect=_fake_get)
    monkeypatch.setattr(carbon.httpx, "get", get)

    carbon.fetch(date(2026, 10, 23))

    urls = {call.args[0] for call in get.call_args_list}
    assert urls == {
        carbon.ODRE_RECORDS_URL.format(dataset=carbon.DATASET_REALTIME),
        carbon.ODRE_RECORDS_URL.format(dataset=carbon.DATASET_CONSOLIDATED),
    }
    assert "taux_co2" in get.call_args.kwargs["params"]["select"]


def test_fetch_skips_days_without_a_measure(rte):
    result = carbon.fetch(date(2026, 10, 23))

    assert date(2026, 10, 27) not in result
    assert result[date(2026, 10, 26)] == (
        pytest.approx(16.3015873),
        carbon.DATASET_REALTIME,
    )


def test_refresh_prefers_consolidated_values_over_real_time(
    factory, monkeypatch
):
    realtime = {"results": [{"date": "2026-08-30", "g_per_kwh": 99.0}]}
    consolidated = {"results": [{"date": "2026-08-30", "g_per_kwh": 12.5}]}

    def get(url, params=None, timeout=None):
        response = mock.Mock()
        is_consolidated = carbon.DATASET_CONSOLIDATED in url
        response.json.return_value = (
            consolidated if is_consolidated else realtime
        )
        return response

    monkeypatch.setattr(carbon.httpx, "get", get)

    with factory() as db:
        carbon.refresh(db, date(2026, 8, 30))
        row = db.scalar(select(CarbonIntensity))

    assert (row.g_per_kwh, row.source) == (12.5, carbon.DATASET_CONSOLIDATED)


def test_refresh_updates_a_day_in_place_when_ingested_twice(factory, rte):
    with factory() as db:
        carbon.refresh(db, date(2026, 10, 23))
        carbon.refresh(db, date(2026, 10, 23))
        rows = db.scalars(select(CarbonIntensity)).all()

    assert len(rows) == 7


def _line(energy_kwh):
    start = datetime(2026, 10, 26)
    return CostLine(
        x_source="opencost",
        provider_name="CNP on-prem",
        service_name="Kubernetes CPU",
        service_category="Compute",
        charge_period_start=start,
        charge_period_end=start + timedelta(days=1),
        resource_id="onprem/qcm/cpu",
        x_cost_eur=1.0,
        billed_cost=1.0,
        list_cost=1.0,
        effective_cost=1.0,
        x_energy_kwh=energy_kwh,
    )


def test_apply_to_lines_converts_kwh_with_the_daily_intensity():
    lines = [_line(2.0)]

    carbon.apply_to_lines(lines, 50.0)

    assert lines[0].x_co2e_kg == pytest.approx(0.1)
    assert lines[0].x_carbon_method == "rte-direct"


def test_apply_to_lines_leaves_carbon_empty_when_intensity_is_missing():
    lines = [_line(2.0)]

    carbon.apply_to_lines(lines, None)

    assert (lines[0].x_co2e_kg, lines[0].x_carbon_method) == (None, None)


def test_apply_to_lines_ignores_lines_without_measured_energy():
    lines = [_line(None)]

    carbon.apply_to_lines(lines, 50.0)

    assert lines[0].x_co2e_kg is None


def test_savings_entries_carry_the_co2e_avoided_by_sleeping(tco):
    rows = [
        savings.NamespaceDay(
            namespace="shop-api",
            asleep_hours=6,
            cpu_cores=1,
            ram_gib=0,
            awake_busy_cores=0.4,
        )
    ]

    [entry] = savings.to_entries(
        date(2026, 10, 27),
        rows,
        {"shop-api": "shop"},
        inputs.NodeRates(0.004, 0.0005),
        tco,
        50.0,
    )

    assert entry.co2e_avoided_kg == pytest.approx(0.036 * 50 / 1000)


def _ingest(monkeypatch, factory, tco_dir):
    monkeypatch.setattr(settings, "FINOPS_DATA_DIR", tco_dir)
    monkeypatch.setattr(settings, "FINOPS_INGEST_SINCE", "2026-10-24")
    monkeypatch.setattr(ingest, "SessionLocal", factory)
    monkeypatch.setattr(ingest, "in_cluster", lambda: False)
    monkeypatch.setattr(ingest.fx, "refresh", lambda db: None)
    monkeypatch.setattr(
        ingest.opencost,
        "fetch_day",
        lambda day, url=None: {"qcm": {"cpuCost": 0.1}},
    )
    monkeypatch.setattr(
        ingest.ai_gateway, "fetch_day", mock.Mock(return_value=[])
    )
    monkeypatch.setattr(ingest.savings, "targets", lambda: {})


def test_ingest_once_sets_co2e_on_onprem_lines_from_the_day_intensity(
    factory, rte, monkeypatch
):
    _ingest(monkeypatch, factory, str(FIXTURES / "finops"))

    ingest.ingest_once(date(2026, 10, 27))

    with factory() as db:
        line = db.scalar(
            select(CostLineItem).where(
                CostLineItem.charge_period_start == datetime(2026, 10, 26),
                CostLineItem.x_source == "opencost",
            )
        )
    assert line.x_carbon_method == "rte-direct"
    assert line.x_co2e_kg == pytest.approx(
        line.x_energy_kwh * 16.3015873 / 1000
    )


def test_ingest_once_leaves_co2e_empty_when_rte_is_unreachable(
    factory, monkeypatch
):
    _ingest(monkeypatch, factory, str(FIXTURES / "finops"))
    monkeypatch.setattr(
        carbon, "refresh", mock.Mock(side_effect=RuntimeError("rte down"))
    )

    written = ingest.ingest_once(date(2026, 10, 27))

    with factory() as db:
        line = db.scalar(select(CostLineItem))
    assert written["opencost"] > 0
    assert (line.x_co2e_kg, line.x_carbon_method) == (None, None)


@pytest.fixture
def client(factory, monkeypatch):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: False)
    monkeypatch.setattr(router, "_user_project_names", lambda token: ["qcm"])
    monkeypatch.setattr(
        router, "_require_project_role", lambda db, token, project, *a: None
    )

    def _db():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


def _add_line(
    factory,
    days_ago,
    kwh,
    co2e,
    project="qcm",
    app="api",
    resource="cpu",
    estimate=True,
):
    start = datetime.combine(
        TODAY - timedelta(days=days_ago), datetime.min.time()
    )
    with factory() as db:
        db.add(
            CostLineItem(
                x_source="opencost",
                provider_name="CNP on-prem",
                service_name="Kubernetes CPU",
                service_category="Compute",
                charge_period_start=start,
                charge_period_end=start + timedelta(days=1),
                billing_period=f"{start:%Y-%m}",
                resource_id=f"onprem/{project}/{app}/{resource}",
                project=project,
                app=app,
                x_energy_kwh=kwh,
                x_co2e_kg=co2e,
                x_is_estimate=estimate,
            )
        )
        db.commit()


def _add_intensity(factory, days_ago, g_per_kwh):
    with factory() as db:
        db.add(
            CarbonIntensity(
                day=TODAY - timedelta(days=days_ago),
                zone="FR",
                g_per_kwh=g_per_kwh,
                method="direct",
                source=carbon.DATASET_REALTIME,
            )
        )
        db.commit()


def test_carbon_returns_zeros_and_both_method_labels_when_nothing_ingested(
    client,
):
    body = client.get("/api/finops/carbon").json()

    assert body["totals"] == {
        "energy_kwh": 0,
        "co2e_direct_kg": 0,
        "co2e_lca_kg": 0,
        "co2e_avoided_kg": 0,
    }
    assert [m["id"] for m in body["methods"]] == ["direct", "lca"]
    assert body["latest"] is None


def test_carbon_lca_total_is_energy_times_the_ademe_factor(factory, client):
    _add_line(factory, 1, kwh=10.0, co2e=0.2)

    totals = client.get("/api/finops/carbon").json()["totals"]

    assert totals["co2e_direct_kg"] == 0.2
    assert totals["co2e_lca_kg"] == pytest.approx(
        10.0 * carbon.LCA_G_PER_KWH / 1000, abs=1e-4
    )


def test_carbon_timeline_pairs_daily_energy_with_that_day_intensity(
    factory, client
):
    _add_line(factory, 1, kwh=4.0, co2e=0.06)
    _add_line(factory, 1, kwh=1.0, co2e=0.015, resource="ram")
    _add_intensity(factory, 1, 15.0)

    [day] = client.get("/api/finops/carbon").json()["timeline"]

    assert day == {
        "date": (TODAY - timedelta(days=1)).isoformat(),
        "energy_kwh": 5.0,
        "co2e_kg": 0.075,
        "g_per_kwh": 15.0,
    }


def test_carbon_rows_are_sorted_by_energy_desc(factory, client):
    _add_line(factory, 1, kwh=1.0, co2e=0.01, app="small")
    _add_line(factory, 1, kwh=9.0, co2e=0.09, app="big")

    rows = client.get("/api/finops/carbon").json()["rows"]

    assert [r["app"] for r in rows] == ["big", "small"]


def test_carbon_latest_is_the_newest_stored_intensity(factory, client):
    _add_intensity(factory, 3, 20.0)
    _add_intensity(factory, 1, 14.52)

    latest = client.get("/api/finops/carbon").json()["latest"]

    assert latest == {
        "g_per_kwh": 14.5,
        "day": (TODAY - timedelta(days=1)).isoformat(),
        "source": carbon.DATASET_REALTIME,
    }


def test_carbon_avoided_comes_from_the_offhours_ledger(factory, client):
    with factory() as db:
        db.add(
            SavingsEntry(
                day=TODAY - timedelta(days=1),
                project="qcm",
                app="api",
                category="offhours",
                co2e_avoided_kg=0.5,
            )
        )
        db.commit()

    totals = client.get("/api/finops/carbon").json()["totals"]

    assert totals["co2e_avoided_kg"] == 0.5


def test_carbon_hides_projects_the_user_does_not_belong_to(factory, client):
    _add_line(factory, 1, kwh=1.0, co2e=0.01, project="qcm")
    _add_line(factory, 1, kwh=100.0, co2e=1.0, project="secret")

    body = client.get("/api/finops/carbon").json()

    assert {r["project"] for r in body["rows"]} == {"qcm"}


def test_carbon_period_filter_excludes_lines_older_than_the_window(
    factory, client
):
    _add_line(factory, 5, kwh=1.0, co2e=0.01)
    _add_line(factory, 45, kwh=10.0, co2e=0.1)

    def energy(period):
        url = f"/api/finops/carbon?period={period}"
        return client.get(url).json()["totals"]["energy_kwh"]

    assert (energy("30d"), energy("90d")) == (1.0, 11.0)


def test_carbon_flags_estimate_when_a_line_is_built_on_mock_inputs(
    factory, client
):
    _add_line(factory, 1, kwh=1.0, co2e=0.01, estimate=True)

    assert client.get("/api/finops/carbon").json()["has_estimate"] is True
