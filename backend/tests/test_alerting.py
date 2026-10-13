from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.alerting import AlertSetting
from app.routers import alerting as alerting_router
from app.services import alerts_grafana, keycloak_service
from app.services.alerts_catalog import BY_ID, namespace_matcher
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import (  # noqa: F401
    add_deployment,
    session_factory,
)


@pytest.fixture
def make(session_factory):
    started = []

    def build(role):
        add_deployment(session_factory, "fullstack")
        grafana = mock.MagicMock()
        grafana.org_id = mock.AsyncMock(return_value=7)
        grafana.rule_states = mock.AsyncMock(
            return_value={alerts_grafana.rule_uid("restarts", None): "firing"}
        )
        grafana.upsert_rule = mock.AsyncMock()
        grafana.delete_rule = mock.AsyncMock()
        grafana.sync_notifications = mock.AsyncMock()
        grafana.rule_uid = alerts_grafana.rule_uid
        grafana.GrafanaAlertingError = alerts_grafana.GrafanaAlertingError
        app = FastAPI()
        app.include_router(alerting_router.router, prefix="/api")

        def override_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "u1",
            "preferred_username": "alice",
        }
        patches = [
            mock.patch.object(alerting_router, "grafana", grafana),
            mock.patch.object(
                keycloak_service, "get_project_role", lambda u, p: role
            ),
            mock.patch.object(
                alerting_router,
                "namespace_projects",
                lambda: {
                    "demo-system": "demo",
                    "demo-shop": "demo",
                    "other-x": "o",
                },
            ),
            mock.patch.object(
                alerting_router.targets_store,
                "read_targets",
                mock.AsyncMock(
                    return_value={"project": "https://discord.test/x"}
                ),
            ),
        ]
        for p in patches:
            p.start()
        started.extend(patches)
        return TestClient(app), grafana

    yield build
    for p in started:
        p.stop()


def test_members_see_the_catalogue_with_live_state(make, session_factory):
    client, _ = make("member")
    with session_factory() as db:
        db.add(
            AlertSetting(
                project="demo",
                app="",
                alert_id="restarts",
                enabled=True,
                params="{}",
                namespaces="demo-shop,demo-system",
            )
        )
        db.commit()

    body = client.get("/api/alerting?project=demo").json()

    restarts = next(i for i in body["items"] if i["id"] == "restarts")
    assert (restarts["state"], body["can_edit"]) == ("firing", False)


def test_members_cannot_switch_alerts(make):
    client, _ = make("member")

    response = client.put(
        "/api/alerting/restarts?project=demo", json={"enabled": True}
    )

    assert response.status_code == 403


def test_enabling_watches_exactly_the_projects_namespaces(make):
    client, grafana = make("admin")

    client.put(
        "/api/alerting/restarts?project=demo",
        json={"enabled": True, "params": {"restarts": 5}},
    )

    args = grafana.upsert_rule.await_args.args
    assert (args[4], args[5]) == (
        {"restarts": 5},
        ["demo-shop", "demo-system"],
    )


def test_an_app_alert_watches_only_its_namespace(make):
    client, grafana = make("admin")

    client.put(
        "/api/alerting/restarts?project=demo&app=shop", json={"enabled": True}
    )

    assert grafana.upsert_rule.await_args.args[5] == ["demo-shop"]


def test_out_of_range_threshold_is_refused(make):
    client, grafana = make("admin")

    response = client.put(
        "/api/alerting/memory_near_limit?project=demo",
        json={"enabled": True, "params": {"percent": 150}},
    )

    assert (response.status_code, grafana.upsert_rule.await_count) == (400, 0)


def test_disabling_removes_the_grafana_rule(make):
    client, grafana = make("admin")

    client.put("/api/alerting/restarts?project=demo", json={"enabled": False})

    assert grafana.delete_rule.await_args.args == (7, "restarts", None)


def test_rule_uids_fit_grafanas_limit():
    assert len(alerts_grafana.rule_uid("memory_near_limit", "a" * 90)) <= 40


def test_namespace_matcher_refuses_anything_but_namespace_names():
    with pytest.raises(ValueError):
        namespace_matcher(['x"} or vector(1) #'])


def test_every_catalogue_expression_ends_with_its_threshold():
    ns = namespace_matcher(["demo-shop"])
    for template in BY_ID.values():
        assert " > " in template.expr(ns, template.defaults())
