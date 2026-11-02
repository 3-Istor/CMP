import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.audit import ActivityRecord, AuditEvent
from app.models.deployment import Deployment, DeploymentStatus
from app.models.finops import CostAlert, CostLineItem
from app.models.release import ReleaseEvent
from app.routers import finops as router
from app.services.finops import alert_poller, anomalies
from app.services.finops.anomalies import NO_CAUSE, detect
from app.services.finops.provider import (
    RealCostProvider,
    SimulatedCostProvider,
)

SPIKE_DAY = date(2026, 11, 20)
SHA = "4f2a9c1d8e7b6a5f4e3d2c1b0a9f8e7d6c5b4a39"


def _flat(value, days=14, before=SPIKE_DAY):
    return {before - timedelta(days=i): value for i in range(1, days + 1)}


# ── detection thresholds ─────────────────────────────────────────────────────


def test_day_above_median_by_threshold_and_minimum_is_flagged():
    daily = {**_flat(1.0), SPIKE_DAY: 1.6}

    found = detect(daily, SPIKE_DAY, pct=0.5, min_eur=0.05)

    assert [(a.day, a.baseline_eur) for a in found] == [(SPIKE_DAY, 1.0)]


def test_day_below_relative_threshold_is_not_flagged():
    daily = {**_flat(1.0), SPIKE_DAY: 1.4}

    assert detect(daily, SPIKE_DAY, pct=0.5, min_eur=0.05) == []


def test_large_relative_jump_on_cents_is_not_flagged():
    daily = {**_flat(0.02), SPIKE_DAY: 0.06}

    assert detect(daily, SPIKE_DAY, pct=0.5, min_eur=0.05) == []


def test_day_without_enough_baseline_is_not_flagged():
    daily = {**_flat(1.0, days=3), SPIKE_DAY: 5.0}

    assert detect(daily, SPIKE_DAY, pct=0.5, min_eur=0.05) == []


def test_an_earlier_spike_does_not_raise_the_baseline():
    daily = {**_flat(1.0), SPIKE_DAY - timedelta(days=2): 9.0, SPIKE_DAY: 2.0}

    found = detect(daily, SPIKE_DAY, pct=0.5, min_eur=0.05)

    assert [a.day for a in found] == [SPIKE_DAY]


def test_thresholds_default_to_settings(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_ANOMALY_PCT", 2.0)
    daily = {**_flat(1.0), SPIKE_DAY: 2.5}

    assert detect(daily, SPIKE_DAY) == []


# ── cause correlation ────────────────────────────────────────────────────────


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _line(day: date, cost: float, namespace: str, project: str):
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
        for app_id, name, project in ((1, "api", "qcm"), (2, "web", "other")):
            namespace = f"{project}-{name}"
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
            for day, cost in {**_flat(1.0), SPIKE_DAY: 3.0}.items():
                db.add(_line(day, cost, namespace, project))
        db.commit()
    return factory


def _sync(db, when: datetime, target="qcm-api", app="api"):
    db.add(
        ActivityRecord(
            external_id=f"deploy-{target}-{when.isoformat()}",
            source="deployment",
            time=when,
            project="qcm",
            app=app,
            actor="Argo CD",
            action="deployment.sync",
            target=target,
            outcome="success",
            details=json.dumps(
                {
                    "revisions": {"https://github.com/3-Istor/qcm": SHA},
                    "automated": True,
                }
            ),
        )
    )
    db.commit()


def _find(factory, deployments=None):
    with factory() as db:
        deployments = deployments or db.query(Deployment).all()
        return anomalies.find_anomalies(
            db,
            RealCostProvider(factory),
            [d for d in deployments if d.project_id == "qcm"],
            SPIKE_DAY,
            SPIKE_DAY,
        )


def test_argocd_sync_the_day_before_is_the_probable_cause(seeded):
    with seeded() as db:
        _sync(db, datetime(2026, 11, 19, 16, 30, tzinfo=timezone.utc))

    [row] = _find(seeded)

    assert row["cause"] == "Sync Argo CD qcm-api (4f2a9c1)"


def test_sync_revision_is_returned_with_the_event(seeded):
    with seeded() as db:
        _sync(db, datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc))

    [row] = _find(seeded)

    assert row["events"][0]["revision"] == "4f2a9c1"


def test_sync_older_than_the_day_before_is_not_a_cause(seeded):
    with seeded() as db:
        _sync(db, datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc))

    [row] = _find(seeded)

    assert row["cause"] == NO_CAUSE


def test_sync_of_another_app_is_not_a_cause(seeded):
    with seeded() as db:
        _sync(
            db,
            datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc),
            target="qcm-worker",
            app="worker",
        )

    [row] = _find(seeded)

    assert row["cause"] == NO_CAUSE


def test_replica_change_made_in_the_cmp_is_described(seeded):
    with seeded() as db:
        db.add(
            AuditEvent(
                created_at=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
                actor="alice",
                action="app.config.update",
                project="qcm",
                app="api",
                target="1",
                outcome="success",
                status_code=200,
                details=json.dumps(
                    {
                        "file": "values.yaml",
                        "changes": [
                            {"path": "replicas", "before": 1, "after": 3},
                            {"path": "env.LOG", "before": "a", "after": "b"},
                        ],
                    }
                ),
            )
        )
        db.commit()

    [row] = _find(seeded)

    assert row["cause"] == "Config modifiée par alice : replicas 1 → 3"


def test_rollout_is_a_cause_with_its_image_tag(seeded):
    with seeded() as db:
        db.add(
            ReleaseEvent(
                deployment_id=1,
                revision=7,
                image="ghcr.io/3-istor/qcm-api:v1.4.0",
                strategy="canary",
                started_at=datetime(2026, 11, 20, 10, tzinfo=timezone.utc),
                outcome="succeeded",
            )
        )
        db.commit()

    [row] = _find(seeded)

    assert row["cause"] == "Release r7 (canary, succeeded) image v1.4.0"


def test_project_wide_lines_do_not_make_an_app_anomalous(factory):
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
        for day in _flat(1.0):
            db.add(_line(day, 1.0, "qcm-api", "qcm"))
        db.add(_line(SPIKE_DAY, 1.0, "qcm-api", "qcm"))
        ai = _line(SPIKE_DAY, 50.0, "qcm/agent/gpt", "qcm")
        ai.x_source = "ai_gateway"
        db.add(ai)
        db.commit()

    assert _find(factory) == []


# ── spike alerts ─────────────────────────────────────────────────────────────


def test_spike_alert_is_stored_once_per_app_and_day(seeded):
    rows = _find(seeded)
    with seeded() as db:
        first = anomalies.record_alerts(db, rows)
        second = anomalies.record_alerts(db, rows)
        stored = db.scalar(
            select(func.count()).select_from(
                CostAlert
            )  # pylint: disable=not-callable
        )

    assert (len(first), len(second), stored) == (1, 0, 1)


@pytest.fixture
def poller(seeded, monkeypatch):
    sent = []

    async def record(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "real")
    monkeypatch.setattr(alert_poller, "SessionLocal", seeded)
    monkeypatch.setattr(
        alert_poller, "get_cost_provider", lambda: RealCostProvider(seeded)
    )
    monkeypatch.setattr(
        alert_poller.discord_service, "send_cost_anomaly", record
    )
    return sent


def test_poller_notifies_each_anomaly_once(poller):
    today = SPIKE_DAY + timedelta(days=1)

    asyncio.run(alert_poller.check_anomalies(today))
    asyncio.run(alert_poller.check_anomalies(today))

    assert sorted(s["app_name"] for s in poller) == ["api", "web"]


def test_spike_alert_message_names_the_cause(seeded, poller):
    with seeded() as db:
        _sync(db, datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc))

    asyncio.run(alert_poller.check_anomalies(SPIKE_DAY + timedelta(days=1)))

    with seeded() as db:
        alert = db.scalars(
            select(CostAlert).where(CostAlert.app_id == 1)
        ).one()
    assert alert.message.endswith(
        "Cause probable : Sync Argo CD qcm-api (4f2a9c1)"
    )


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def api(seeded, monkeypatch):
    monkeypatch.setattr(
        router, "get_cost_provider", lambda: RealCostProvider(seeded)
    )
    monkeypatch.setattr(
        router,
        "period_to_range",
        lambda p: (
            SPIKE_DAY - timedelta(days=5),
            SPIKE_DAY + timedelta(days=1),
        ),
    )


def test_anomalies_endpoint_lists_only_projects_the_user_belongs_to(
    seeded, api, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: False)
    monkeypatch.setattr(router, "_user_project_names", lambda token: ["qcm"])
    with seeded() as db:
        rows = router.get_anomalies({}, db, None, "30d")

    assert [(r.app_name, r.date) for r in rows] == [("api", "2026-11-20")]


def test_anomalies_endpoint_lets_a_cnp_admin_see_every_project(
    seeded, api, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    with seeded() as db:
        rows = router.get_anomalies({}, db, None, "30d")

    assert {r.project_id for r in rows} == {"qcm", "other"}


def test_anomalies_endpoint_denies_a_project_the_user_has_no_role_on(
    seeded, api, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: False)

    def deny(*_args):
        raise HTTPException(status_code=403)

    monkeypatch.setattr(router, "assert_project_role", deny)
    with seeded() as db, pytest.raises(HTTPException) as err:
        router.get_anomalies({}, db, "other", "30d")

    assert err.value.status_code == 403


def test_anomalies_endpoint_skips_today_still_being_ingested(
    seeded, api, monkeypatch
):
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: True)
    monkeypatch.setattr(
        router,
        "period_to_range",
        lambda p: (SPIKE_DAY - timedelta(days=5), SPIKE_DAY),
    )
    with seeded() as db:
        assert router.get_anomalies({}, db, "qcm", "30d") == []


def test_anomalies_also_run_on_simulated_costs(factory):
    deployment = SimpleNamespace(
        id=1,
        name="api",
        project_id="qcm",
        k8s_namespace="qcm-api",
        argocd_app_name=None,
        created_at=datetime(2026, 1, 1),
        app_config=None,
    )
    with factory() as db:
        rows = anomalies.find_anomalies(
            db,
            SimulatedCostProvider(),
            [deployment],
            date(2026, 10, 1),
            date(2026, 10, 31),
        )

    assert all(r["app_id"] == 1 for r in rows)
