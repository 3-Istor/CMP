from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.observability import SloSetting
from app.routers import observability as router
from app.services import authz, keycloak_service
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import (  # noqa: F401
    add_deployment,
    session_factory,
)

SILENCE = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"


@pytest.fixture
def make(session_factory):
    started = []

    def build(role, enabled=True):
        add_deployment(session_factory, "static")
        app = FastAPI()
        app.include_router(router.router, prefix="/api")

        def override_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "u1",
            "preferred_username": "alice",
        }
        silences = mock.MagicMock(wraps=router.silences)
        silences.create = mock.AsyncMock(return_value=SILENCE)
        silences.list_for = mock.AsyncMock(return_value=[])
        silences.expire = mock.AsyncMock()
        silences.SilenceNotFoundError = router.silences.SilenceNotFoundError
        silences.AlertmanagerError = router.silences.AlertmanagerError
        sync = mock.Mock()
        patches = [
            mock.patch.object(authz, "SessionLocal", session_factory),
            mock.patch.object(
                keycloak_service, "get_project_role", lambda u, p: role
            ),
            mock.patch.object(router.settings, "OBS_V3_ENABLED", enabled),
            mock.patch.object(router, "silences", silences),
            mock.patch.object(router.reconcile, "request_sync", sync),
        ]
        for p in patches:
            p.start()
        started.extend(patches)
        return TestClient(app), silences, sync

    yield build
    for p in started:
        p.stop()


def test_nothing_is_served_while_the_flag_is_off(make):
    client, _, _ = make("admin", enabled=False)

    response = client.get("/api/observability/slo?project=demo&app=shop")

    assert response.status_code == 404


def test_members_read_the_default_slo(make):
    client, _, _ = make("member")

    body = client.get("/api/observability/slo?project=demo&app=shop").json()

    assert (body["availability"], body["period_days"], body["can_edit"]) == (
        99.5,
        28,
        False,
    )


def test_members_cannot_change_the_target(make):
    client, _, _ = make("member")

    response = client.put(
        "/api/observability/slo",
        json={
            "project": "demo",
            "app": "shop",
            "availability": 99,
            "latency": 95,
        },
    )

    assert response.status_code == 403


def test_an_admin_target_is_stored_and_synced(make, session_factory):
    client, _, sync = make("admin")

    client.put(
        "/api/observability/slo",
        json={
            "project": "demo",
            "app": "shop",
            "availability": 99.9,
            "latency": 90,
        },
    )

    with session_factory() as db:
        stored = db.query(SloSetting).one()
    assert (stored.availability, sync.call_count) == (99.9, 1)


def test_a_target_out_of_range_is_refused(make):
    client, _, _ = make("admin")

    response = client.put(
        "/api/observability/slo",
        json={
            "project": "demo",
            "app": "shop",
            "availability": 100,
            "latency": 95,
        },
    )

    assert response.status_code == 422


def test_guests_cannot_mute(make):
    client, silences, _ = make("guest")

    response = client.post(
        "/api/observability/silences", json={"project": "demo", "hours": 1}
    )

    assert (response.status_code, silences.create.call_count) == (403, 0)


def test_a_member_mutes_with_the_project_matcher_forced(make):
    client, silences, _ = make("member")

    response = client.post(
        "/api/observability/silences",
        json={"project": "demo", "matchers": {"cnp_app": "shop"}, "hours": 2},
    )

    sent = silences.create.call_args.args[0]["matchers"]
    assert (response.status_code, sent[0]["name"], sent[0]["value"]) == (
        201,
        "cnp_project",
        "demo",
    )


def test_a_member_cannot_mute_another_project_through_the_matchers(make):
    client, silences, _ = make("member")

    response = client.post(
        "/api/observability/silences",
        json={
            "project": "demo",
            "matchers": {"cnp_project": "other"},
            "hours": 2,
        },
    )

    assert (response.status_code, silences.create.call_count) == (400, 0)


def test_a_non_member_cannot_mute(make):
    client, silences, _ = make(None)

    response = client.post(
        "/api/observability/silences", json={"project": "demo", "hours": 1}
    )

    assert (response.status_code, silences.create.call_count) == (403, 0)


def test_expiring_checks_the_silence_belongs_to_the_project(make):
    client, silences, _ = make("member")
    silences.expire.side_effect = router.silences.SilenceNotFoundError(SILENCE)

    response = client.delete(
        f"/api/observability/silences/{SILENCE}?project=demo"
    )

    assert (response.status_code, silences.expire.call_args.args) == (
        404,
        ("demo", SILENCE),
    )


def test_a_malformed_silence_id_never_reaches_alertmanager(make):
    client, silences, _ = make("member")

    response = client.delete(
        "/api/observability/silences/..%2F..%2Fstatus?project=demo"
    )

    assert (response.status_code, silences.expire.call_count) == (404, 0)
