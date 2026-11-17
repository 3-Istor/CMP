import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.models.audit import AuditEvent
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.security import SecurityException
from app.routers import activity as activity_router
from app.routers import deployments as deployments_router
from app.routers import finops as finops_router
from app.services import audit, authz, keycloak_service
from app.services.finops.recommendations import Recommendation
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
        app.add_middleware(audit.AuditMiddleware)
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
            mock.patch.object(authz, "SessionLocal", session_factory),
            mock.patch.object(
                audit,
                "decode_platform_token",
                lambda token: {"preferred_username": "alice"},
            ),
            mock.patch.object(
                deployments_router, "get_file_content", repo.get
            ),
            mock.patch.object(
                deployments_router, "put_file_content", repo.put
            ),
            mock.patch.object(
                deployments_router,
                "get_org_installation_token",
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

    response = client.get("/api/activity?project=demo&source=cmp")

    assert response.json()["events"][0]["source_ip"] is None


def test_activity_of_a_project_is_refused_to_outsiders(build):
    client, _ = build(STATIC_FILES, None)

    response = client.get("/api/activity?project=demo")

    assert response.status_code == 403


def test_activity_of_a_project_is_refused_to_guests(build):
    client, _ = build(STATIC_FILES, "guest")

    response = client.get("/api/activity?project=demo")

    assert response.status_code == 403


def test_export_is_refused_to_guests(build):
    client, _ = build(STATIC_FILES, "guest")

    response = client.get("/api/activity/export?project=demo")

    assert response.status_code == 403


def seed(session_factory, rows):
    with session_factory() as db:
        for project, app, actor, action in rows:
            db.add(
                AuditEvent(
                    actor=actor,
                    action=action,
                    project=project,
                    app=app,
                    target=app,
                    outcome="success",
                    status_code=200,
                )
            )
        db.commit()


ROWS = [
    ("demo", "web", "alice", "app.sleep"),
    ("demo", "api", "bob", "app.config.update"),
    ("other", "secret-app", "carol", "app.delete"),
]


def test_a_project_never_sees_the_rows_of_another_project(
    build, session_factory
):
    client, _ = build(STATIC_FILES, "member")
    seed(session_factory, ROWS)

    events = client.get("/api/activity?project=demo&source=cmp").json()[
        "events"
    ]

    assert {e["project"] for e in events} == {"demo"}


def test_export_never_contains_the_rows_of_another_project(
    build, session_factory
):
    client, _ = build(STATIC_FILES, "member")
    seed(session_factory, ROWS)

    response = client.get("/api/activity/export?project=demo&source=cmp")

    assert "secret-app" not in response.text
    assert "carol" not in response.text


def test_export_is_allowed_to_members_as_csv(build, session_factory):
    client, _ = build(STATIC_FILES, "member")
    seed(session_factory, ROWS)

    response = client.get("/api/activity/export?project=demo&source=cmp")

    assert response.headers["content-type"].startswith("text/csv")
    assert "app.sleep" in response.text


def test_app_filter_keeps_only_that_app(build, session_factory):
    client, _ = build(STATIC_FILES, "member")
    seed(session_factory, ROWS)

    events = client.get(
        "/api/activity?project=demo&source=cmp&app=web"
    ).json()["events"]

    assert [e["app"] for e in events] == ["web"]


def test_filters_apply_before_the_page_limit(build, session_factory):
    client, _ = build(STATIC_FILES, "member")
    seed(
        session_factory,
        [("demo", "web", "alice", "app.sleep")]
        + [("demo", "api", "bob", "app.restart")] * 5,
    )

    events = client.get(
        "/api/activity?project=demo&source=cmp&actor=alice&limit=2"
    ).json()["events"]

    assert [e["actor"] for e in events] == ["alice"]


def test_platform_admin_sees_every_project(build, session_factory):
    client, _ = build(STATIC_FILES, None)
    client.app.dependency_overrides[get_current_user] = lambda: {
        "sub": "root",
        "preferred_username": "root",
        "realm_access": {"roles": ["cnp-admin"]},
    }
    seed(session_factory, ROWS)

    with mock.patch.object(activity_router, "is_cnp_admin", lambda t: True):
        events = client.get("/api/activity?source=cmp").json()["events"]

    assert {e["project"] for e in events} == {"demo", "other"}


def test_export_neutralises_spreadsheet_formulas():
    assert activity_router._csv_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"


def test_activity_events_carry_the_app_id_of_live_apps_only(
    build, session_factory
):
    client, shop_id = build(STATIC_FILES, "member")
    with session_factory() as db:
        db.add(
            Deployment(
                name="gone",
                template_id="k3s-gitops-app",
                provider_type=ProviderType.KUBERNETES,
                project_id="demo",
                status=DeploymentStatus.DELETED,
            )
        )
        db.commit()
    seed(
        session_factory,
        [
            ("demo", "shop", "alice", "app.sleep"),
            ("demo", "gone", "bob", "app.delete"),
            ("demo", None, "bob", "project.member.add"),
        ],
    )

    events = client.get("/api/activity?project=demo&source=cmp").json()[
        "events"
    ]

    assert {e["app"]: e["app_id"] for e in events} == {
        "shop": shop_id,
        "gone": None,
        None: None,
    }


def test_activity_feed_states_its_retention(build):
    client, _ = build(STATIC_FILES, "member")

    body = client.get("/api/activity?project=demo&source=cmp").json()

    assert body["retention_days"] == settings.AUDIT_RETENTION_DAYS


def test_recommendation_actions_are_journaled_in_their_project(
    build, session_factory, monkeypatch
):
    _, shop_id = build(STATIC_FILES, "admin")
    rec = Recommendation(
        id=f"{shop_id}:rightsizing",
        app_id=shop_id,
        app_name="shop",
        project_id="demo",
        rec_type="rightsizing",
        title="t",
        justification="j",
        current={},
        recommended={},
        monthly_saving_eur=1.0,
        confidence=80,
        effort="medium",
    )
    provider = mock.Mock()
    provider.recommendations.return_value = [rec]
    monkeypatch.setattr(finops_router, "get_cost_provider", lambda: provider)
    monkeypatch.setattr(
        finops_router, "_require_project_role", lambda *a: None
    )
    app = FastAPI()
    app.add_middleware(audit.AuditMiddleware)
    app.include_router(finops_router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    client = TestClient(app, headers={"Authorization": "Bearer t"})

    response = client.post(
        f"/api/finops/recommendations/{shop_id}:rightsizing/ignore"
    )

    assert response.status_code == 200
    [event] = journal(session_factory)
    assert (event.action, event.project, event.app) == (
        "finops.recommendation.ignore",
        "demo",
        "shop",
    )


def test_exception_events_are_journaled_in_their_project(
    build, session_factory
):
    build(STATIC_FILES, "admin")
    with session_factory() as db:
        row = SecurityException(
            project="demo",
            app="shop",
            fingerprint="f" * 32,
            rule="leak",
            kind="leak",
            status="false_positive",
            statement="s",
            expires_on=date(2027, 1, 1),
            author="bob",
        )
        db.add(row)
        db.commit()
        exception_id = row.id
    request = Request(
        {"type": "http", "headers": [], "query_string": b"", "client": None}
    )

    asyncio.run(
        audit._record(  # pylint: disable=protected-access
            request,
            "security.exception.revoke",
            {"exception_id": str(exception_id)},
            b"",
            200,
        )
    )

    [event] = journal(session_factory)
    assert (event.project, event.app, event.target) == (
        "demo",
        "shop",
        str(exception_id),
    )
