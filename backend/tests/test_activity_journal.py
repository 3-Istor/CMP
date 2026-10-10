import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.audit import AuditEvent
from app.routers import activity as activity_router
from app.routers import deployments as deployments_router
from app.services import audit, keycloak_service
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import (  # noqa: F401
    BACKEND_YAML,
    FRONTEND_YAML,
    FakeRepo,
    add_deployment,
    session_factory,
)

STATIC_FILES = {"deploy/values.yaml": FRONTEND_YAML}


@pytest.fixture
def build(session_factory):
    started = []

    def make(files: dict[str, str], role: str | None, app_type="static"):
        deployment_id = add_deployment(session_factory, app_type)
        repo = FakeRepo(files)
        app = FastAPI()
        app.middleware("http")(audit.audit_middleware)
        app.include_router(deployments_router.router, prefix="/api")
        app.include_router(activity_router.router, prefix="/api")

        def override_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "user-1",
            "preferred_username": "alice",
        }
        patches = [
            mock.patch.object(audit, "SessionLocal", session_factory),
            mock.patch.object(
                audit,
                "decode_platform_token",
                lambda token: {"preferred_username": "alice"},
            ),
            mock.patch.object(
                deployments_router, "get_file_content", repo.get
            ),
            mock.patch.object(
                deployments_router, "update_file_content", repo.put
            ),
            mock.patch.object(
                deployments_router,
                "get_installation_token",
                mock.AsyncMock(return_value="tok"),
            ),
            mock.patch.object(
                deployments_router,
                "get_project_role",
                lambda user, project: role,
            ),
            mock.patch.object(
                keycloak_service,
                "get_project_role",
                lambda user, project: role,
            ),
        ]
        for patch in patches:
            patch.start()
        started.extend(patches)
        client = TestClient(app, headers={"Authorization": "Bearer t"})
        return client, deployment_id

    yield make
    for patch in started:
        patch.stop()


def journal(session_factory) -> list[AuditEvent]:
    with session_factory() as db:
        return db.query(AuditEvent).order_by(AuditEvent.id).all()


def test_config_change_is_journaled_with_its_author(build, session_factory):
    client, deployment_id = build(STATIC_FILES, "admin")

    client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={"_sha": "sha-deploy/values.yaml", "replicaCount": 2},
    )

    [event] = journal(session_factory)
    assert (event.actor, event.action, event.project, event.app) == (
        "alice",
        "app.config.update",
        "demo",
        "shop",
    )


def test_config_change_records_the_value_it_replaced(build, session_factory):
    client, deployment_id = build(STATIC_FILES, "admin")

    client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={
            "_sha": "sha-deploy/values.yaml",
            "ingress": {"sso_protected": False},
        },
    )

    [event] = journal(session_factory)
    assert json.loads(event.details)["changes"] == [
        {"path": "ingress.sso_protected", "before": True, "after": False}
    ]


def test_refused_change_is_journaled_as_a_failure(build, session_factory):
    client, deployment_id = build(STATIC_FILES, "member")

    client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={"_sha": "sha-deploy/values.yaml", "replicaCount": 2},
    )

    [event] = journal(session_factory)
    assert (event.outcome, event.status_code) == ("failure", 403)


def test_dry_run_is_not_journaled(build, session_factory):
    client, deployment_id = build(STATIC_FILES, "admin")

    client.put(
        f"/api/deployments/{deployment_id}/security-data?dry_run=true",
        json={"exposure": "public"},
    )

    assert journal(session_factory) == []


def test_exposure_change_keeps_the_previous_preset(build, session_factory):
    client, deployment_id = build(STATIC_FILES, "admin")

    client.put(
        f"/api/deployments/{deployment_id}/security-data",
        json={"exposure": "public"},
    )

    [event] = journal(session_factory)
    details = json.loads(event.details)
    assert (details["exposure_before"], details["exposure"]) == (
        "project_users",
        "public",
    )


def test_reads_are_not_journaled(build, session_factory):
    client, deployment_id = build(STATIC_FILES, "admin")

    client.get(f"/api/deployments/{deployment_id}/config")

    assert journal(session_factory) == []


def test_secret_looking_paths_are_masked():
    result = audit.changes(
        {"env": {"DB_PASSWORD": "old"}}, {"env": {"DB_PASSWORD": "new"}}
    )

    assert result == [
        {"path": "env.DB_PASSWORD", "before": "***", "after": "***"}
    ]


def test_unlisted_body_fields_are_kept_by_name_only():
    fields = audit._body_fields(
        json.dumps(
            {"project": "demo", "app_config": {"token": "s3cr3t"}}
        ).encode()
    )

    assert fields == {"project": "demo", "keys": ["app_config"]}


def test_purge_removes_only_events_past_retention(session_factory):
    old = datetime.now(timezone.utc) - timedelta(days=31)
    with session_factory() as db:
        for created_at, action in ((old, "old"), (None, "recent")):
            db.add(
                AuditEvent(
                    actor="alice",
                    action=action,
                    target="",
                    outcome="success",
                    status_code=200,
                    source_ip="",
                    details="{}",
                    **({"created_at": created_at} if created_at else {}),
                )
            )
        db.commit()

    with mock.patch.object(audit, "SessionLocal", session_factory):
        audit.purge_expired()

    assert [e.action for e in journal(session_factory)] == ["recent"]


def test_members_do_not_see_source_ips(build, session_factory):
    client, deployment_id = build(STATIC_FILES, "member")
    client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={"_sha": "sha-deploy/values.yaml", "replicaCount": 2},
    )

    response = client.get("/api/activity?project=demo")

    assert response.json()[0]["source_ip"] is None


def test_activity_of_a_project_is_refused_to_outsiders(build):
    client, _ = build(STATIC_FILES, None)

    response = client.get("/api/activity?project=demo")

    assert response.status_code == 403


def test_export_is_refused_to_members(build):
    client, _ = build(STATIC_FILES, "member")

    response = client.get("/api/activity/export?project=demo")

    assert response.status_code == 403


def test_export_neutralises_spreadsheet_formulas():
    assert activity_router._csv_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
