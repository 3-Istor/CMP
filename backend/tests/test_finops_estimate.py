from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.finops import CostLineItem
from app.services.finops import estimate
from app.services.finops.engine import AppSpec, Quota
from app.services.finops.ingest import inputs

FIXTURES = Path(__file__).parent / "fixtures" / "finops"
TODAY = date(2026, 10, 28)
USD_PER_EUR = 1.08


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_DATA_DIR", str(FIXTURES))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _app(namespace="qcm-api"):
    return AppSpec(
        id=1,
        name=namespace,
        project_id="qcm",
        created_at=datetime(2026, 1, 1),
        quota=Quota(1, 1, 1, 1, 1),
        namespace=namespace,
    )


def _line(db, day, kind, quantity, cost=0.0, cloud="onprem", basis="tco"):
    start = datetime.combine(day, datetime.min.time())
    db.add(
        CostLineItem(
            x_source="opencost",
            provider_name="OpenCost",
            service_name=kind,
            service_category="Compute",
            charge_category="Usage",
            charge_period_start=start,
            charge_period_end=start + timedelta(days=1),
            billing_period=f"{day:%Y-%m}",
            resource_id=f"{cloud}/qcm-api/{kind}",
            resource_name="qcm-api",
            billed_cost=cost,
            effective_cost=cost,
            list_cost=cost,
            billing_currency="EUR",
            consumed_quantity=quantity,
            consumed_unit=None,
            project="qcm",
            app="api",
            cloud=cloud,
            x_cost_eur=cost,
            x_pricing_basis=basis,
            x_is_final=True,
            x_is_estimate=False,
            x_ingested_at=start,
        )
    )
    db.commit()


def test_estimate_prices_average_usage_on_every_cluster(db):
    for offset in (1, 2):
        day = TODAY - timedelta(days=offset)
        _line(db, day, "cpu", quantity=24 * 0.5)
        _line(db, day, "ram", quantity=24 * 2)

    [row] = estimate.for_apps(db, [_app()], TODAY, USD_PER_EUR)
    rates = inputs.cluster_rates(USD_PER_EUR)

    assert (row["avg_cores"], row["avg_ram_gib"]) == (0.5, 2.0)
    assert row["monthly_eur"]["aws"] == pytest.approx(
        rates["aws"].hourly_eur(0.5, 2) * 730, abs=0.01
    )
    assert set(row["monthly_eur"]) == {"onprem", "aws", "gcp"}


def test_estimate_derives_old_on_prem_quantities_from_their_cost(db):
    onprem = inputs.cluster_rates(None)["onprem"]
    day = TODAY - timedelta(days=1)
    _line(db, day, "cpu", None, cost=24 * 0.5 * onprem.cpu_eur_per_core_hour)
    _line(db, day, "ram", 48.0)

    [row] = estimate.for_apps(db, [_app()], TODAY, USD_PER_EUR)

    assert row["avg_cores"] == pytest.approx(0.5)


def test_estimate_ignores_the_provisional_day(db):
    _line(db, TODAY, "cpu", 1.0)

    assert estimate.for_apps(db, [_app()], TODAY, USD_PER_EUR) == []


def test_estimate_keeps_on_prem_only_while_the_usd_rate_is_unknown(db):
    _line(db, TODAY - timedelta(days=1), "cpu", 12.0)

    [row] = estimate.for_apps(db, [_app()], TODAY, None)

    assert set(row["monthly_eur"]) == {"onprem"}


def test_estimate_flags_the_cloud_price_that_is_still_a_placeholder(db):
    _line(db, TODAY - timedelta(days=1), "cpu", 12.0)

    [row] = estimate.for_apps(db, [_app()], TODAY, USD_PER_EUR)

    assert row["estimated"]["gcp"] is True
    assert row["estimated"]["aws"] is False
