from datetime import date, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.finops import SavingsEntry
from app.routers import finops as router
from app.services.keycloak_service import get_current_user

TODAY = date.today()


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


def _add(
    factory,
    days_ago,
    project="qcm",
    app="api",
    category="offhours",
    avoided=0.0,
    realized=0.0,
    kwh=0.0,
):
    with factory() as db:
        db.add(
            SavingsEntry(
                day=TODAY - timedelta(days=days_ago),
                project=project,
                app=app,
                category=category,
                avoided_eur=avoided,
                realized_eur=realized,
                energy_avoided_kwh=kwh,
                co2e_avoided_kg=kwh * 0.1,
            )
        )
        db.commit()


def test_savings_returns_zeros_and_empty_lists_when_ledger_is_empty(client):
    body = client.get("/api/finops/savings").json()

    assert body == {
        "totals": {
            "avoided_eur": 0,
            "realized_eur": 0,
            "energy_avoided_kwh": 0,
            "co2e_avoided_kg": 0,
        },
        "by_category": [],
        "timeline": [],
        "rows": [],
        "since": None,
    }


def test_savings_totals_keep_avoided_and_realized_separate(factory, client):
    _add(factory, 1, avoided=10.0, realized=2.0, kwh=5.0)
    _add(factory, 2, category="idle_cleanup", avoided=4.0, realized=0.0)

    totals = client.get("/api/finops/savings").json()["totals"]

    assert (totals["avoided_eur"], totals["realized_eur"]) == (14.0, 2.0)


def test_savings_timeline_has_two_series_per_day_never_summed(factory, client):
    _add(factory, 1, avoided=10.0, realized=2.0)
    _add(factory, 1, app="web", avoided=1.0, realized=0.5)

    timeline = client.get("/api/finops/savings").json()["timeline"]

    assert timeline == [
        {
            "date": (TODAY - timedelta(days=1)).isoformat(),
            "avoided_eur": 11.0,
            "realized_eur": 2.5,
        }
    ]


def test_savings_rows_are_sorted_by_avoided_desc(factory, client):
    _add(factory, 1, app="small", avoided=1.0)
    _add(factory, 1, app="big", avoided=9.0)
    _add(factory, 2, app="big", avoided=1.0)

    rows = client.get("/api/finops/savings").json()["rows"]

    assert [(r["app"], r["avoided_eur"], r["days"]) for r in rows] == [
        ("big", 10.0, 2),
        ("small", 1.0, 1),
    ]


def test_savings_hides_projects_the_user_does_not_belong_to(factory, client):
    _add(factory, 1, project="qcm", avoided=3.0)
    _add(factory, 1, project="secret", avoided=100.0)

    body = client.get("/api/finops/savings").json()

    assert {r["project"] for r in body["rows"]} == {"qcm"}
    assert body["totals"]["avoided_eur"] == 3.0


def test_savings_lets_a_cnp_admin_see_every_project(
    factory, client, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    _add(factory, 1, project="qcm", avoided=3.0)
    _add(factory, 1, project="secret", avoided=100.0)

    totals = client.get("/api/finops/savings").json()["totals"]

    assert totals["avoided_eur"] == 103.0


def test_savings_project_filter_requires_a_role_on_the_project(
    factory, client, monkeypatch
):
    def deny(db, token, project, *args):
        raise HTTPException(status_code=403, detail="no role")

    monkeypatch.setattr(router, "_require_project_role", deny)
    _add(factory, 1, project="secret", avoided=100.0)

    response = client.get("/api/finops/savings?project=secret")

    assert response.status_code == 403


def test_savings_period_filter_excludes_entries_older_than_the_window(
    factory, client
):
    _add(factory, 5, avoided=1.0)
    _add(factory, 45, avoided=10.0)

    def avoided(period):
        url = f"/api/finops/savings?period={period}"
        return client.get(url).json()["totals"]["avoided_eur"]

    assert (avoided("30d"), avoided("90d")) == (1.0, 11.0)


def test_savings_since_is_the_first_day_with_data(factory, client):
    _add(factory, 3, avoided=1.0)
    _add(factory, 8, avoided=1.0)

    since = client.get("/api/finops/savings?period=30d").json()["since"]

    assert since == (TODAY - timedelta(days=8)).isoformat()


def test_overview_exposes_ytd_savings_with_both_figures_apart(
    factory, client, monkeypatch
):
    monkeypatch.setattr(router, "_load_deployments", lambda *a, **k: [])
    _add(factory, 0, avoided=7.0, realized=1.5)
    _add(factory, 0, project="secret", avoided=50.0, realized=50.0)

    ytd = client.get("/api/finops/overview").json()["savings_ytd"]

    assert ytd == {"avoided_eur": 7.0, "realized_eur": 1.5}
