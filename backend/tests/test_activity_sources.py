import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.routers import activity as activity_router
from app.services import activity_sources as src
from app.services import keycloak_service
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import session_factory  # noqa: F401

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
NAMESPACES = {"shop-system", "shop-web"}


def audit_line(**overrides) -> str:
    event = {
        "verb": "create",
        "user": {"username": "alice@3istor.com", "groups": ["oidc:k3s-dev"]},
        "objectRef": {
            "resource": "pods",
            "namespace": "shop-web",
            "name": "web-1",
            "subresource": "exec",
        },
        "requestURI": "/api/v1/namespaces/shop-web/pods/web-1/exec?command=sh",
        "sourceIPs": ["10.0.0.1"],
        "responseStatus": {"code": 101},
    }
    event.update(overrides)
    return json.dumps(event)


def test_a_shell_in_a_project_pod_is_a_notable_event():
    event = src.kubernetes_event("shop", NAMESPACES, NOW, audit_line(), False)

    assert (event.action, event.notable, event.app) == (
        "kubernetes.exec",
        True,
        "web",
    )


def test_another_projects_namespace_is_ignored():
    line = audit_line(
        objectRef={
            "resource": "pods",
            "namespace": "shopping-web",
            "name": "x",
        }
    )

    assert src.kubernetes_event("shop", NAMESPACES, NOW, line, False) is None


def test_service_account_writes_are_left_to_gitops():
    line = audit_line(
        user={"username": "system:serviceaccount:argocd:controller"},
        objectRef={
            "resource": "deployments",
            "namespace": "shop-web",
            "name": "web",
        },
        verb="patch",
    )

    assert src.kubernetes_event("shop", NAMESPACES, NOW, line, False) is None


def test_the_admin_kubeconfig_counts_as_a_person():
    line = audit_line(
        user={"username": "system:admin"},
        objectRef={
            "resource": "deployments",
            "namespace": "shop-web",
            "name": "web",
        },
        verb="delete",
    )

    assert src.kubernetes_event(
        "shop", NAMESPACES, NOW, line, False
    ).actor == ("system:admin")


def test_reads_are_hidden_unless_asked():
    line = audit_line(
        verb="get",
        objectRef={"resource": "pods", "namespace": "shop-web", "name": "w"},
    )

    assert src.kubernetes_event("shop", NAMESPACES, NOW, line, False) is None


def test_secret_reads_by_a_person_always_show():
    line = audit_line(
        verb="get",
        objectRef={
            "resource": "secrets",
            "namespace": "shop-web",
            "name": "db",
        },
    )

    assert src.kubernetes_event("shop", NAMESPACES, NOW, line, False).notable


def test_vault_path_must_belong_to_the_project():
    line = json.dumps(
        {
            "auth": {"display_name": "oidc-alice"},
            "request": {
                "path": "project-shopping/data/web",
                "operation": "read",
            },
        }
    )

    assert src.vault_event("shop", NOW, line) is None


def test_vault_reads_by_the_secrets_operator_are_skipped():
    line = json.dumps(
        {
            "auth": {
                "display_name": "kubernetes-vault-secrets-operator-vault-secrets-operator"
            },
            "request": {"path": "project-shop/data/web", "operation": "read"},
        }
    )

    assert src.vault_event("shop", NOW, line) is None


def test_keycloak_login_error_is_a_failure():
    line = (
        '2026-10-10 WARN [org.keycloak.events] type="LOGIN_ERROR", realmId="1", '
        'realmName="shop", clientId="web", username="bob", ipAddress="1.2.3.4", '
        'error="invalid_user_credentials"'
    )

    event = src.keycloak_event("shop", NOW, line)

    assert (event.action, event.outcome, event.actor) == (
        "keycloak.login_error",
        "failure",
        "bob",
    )


def test_keycloak_events_of_another_realm_are_ignored():
    line = 'type="LOGIN", realmName="shopping", username="bob"'

    assert src.keycloak_event("shop", NOW, line) is None


def test_logs_query_is_pinned_to_the_project():
    query = src.logs_query("shop", "shop-web", None, None, "timeout", "error")

    assert query.startswith(
        '{project="shop", namespace="shop-web"} |= `timeout`'
    )


@pytest.fixture
def client_for(session_factory):
    started = []

    def make(role, events):
        app = FastAPI()
        app.include_router(activity_router.router, prefix="/api")

        def override_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "u1",
            "preferred_username": "alice",
            "email": "alice@3istor.com",
        }

        async def kubernetes_events(project, start, end, limit, include_reads):
            return [e for e in events if e.source == "kubernetes"]

        async def keycloak_events(project, start, end, limit):
            return [e for e in events if e.source == "keycloak"]

        async def nothing(*args, **kwargs):
            return []

        patches = [
            mock.patch.object(
                keycloak_service, "get_project_role", lambda u, p: role
            ),
            mock.patch.object(src, "kubernetes_events", kubernetes_events),
            mock.patch.object(src, "keycloak_events", keycloak_events),
            mock.patch.object(src, "vault_events", nothing),
            mock.patch.object(src, "deployment_events", lambda p, s, e: []),
        ]
        for patch in patches:
            patch.start()
        started.extend(patches)
        return TestClient(app)

    yield make
    for patch in started:
        patch.stop()


def event(source, actor, action, minutes_ago=5, notable=False):
    return src.Event(
        id=f"{source}-{actor}-{minutes_ago}",
        time=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        source=source,
        actor=actor,
        action=action,
        notable=notable,
        project="shop",
        app="web",
        target="",
        outcome="success",
        status_code=None,
        source_ip="10.0.0.1",
    )


def test_members_see_only_their_own_kubectl_actions(client_for):
    client = client_for(
        "member",
        [
            event("kubernetes", "alice@3istor.com", "kubernetes.exec"),
            event("kubernetes", "bob@3istor.com", "kubernetes.exec"),
        ],
    )

    feed = client.get("/api/activity?project=shop").json()

    assert [e["actor"] for e in feed["events"]] == ["alice@3istor.com"]


def test_members_do_not_see_logins(client_for):
    client = client_for("member", [event("keycloak", "bob", "keycloak.login")])

    feed = client.get("/api/activity?project=shop").json()

    assert feed["events"] == []


def test_admins_see_everyone(client_for):
    client = client_for(
        "admin",
        [
            event("kubernetes", "alice@3istor.com", "kubernetes.exec"),
            event("kubernetes", "bob@3istor.com", "kubernetes.exec"),
        ],
    )

    feed = client.get("/api/activity?project=shop").json()

    assert len(feed["events"]) == 2


def test_an_unreachable_source_is_reported_not_fatal(client_for):
    client = client_for("admin", [])

    async def down(*args, **kwargs):
        raise src.LokiUnavailableError("down")

    with mock.patch.object(src, "kubernetes_events", down):
        feed = client.get("/api/activity?project=shop").json()

    assert feed["unavailable"] == ["kubernetes"]


def test_summary_compares_with_the_previous_period(client_for):
    client = client_for(
        "admin",
        [
            event(
                "kubernetes", "alice@3istor.com", "kubernetes.exec", 60, True
            ),
            event(
                "kubernetes", "bob@3istor.com", "kubernetes.exec", 60 * 24 * 8
            ),
        ],
    )

    summary = client.get("/api/activity/summary?project=shop&days=7").json()

    assert (summary["actions"], summary["notable"]["value"]) == (
        {"value": 1, "previous": 1},
        1,
    )


def test_invalid_project_names_are_refused(client_for):
    client = client_for("admin", [])

    response = client.get("/api/activity/logs", params={"project": 'x"} |= `'})

    assert response.status_code == 400


def test_search_cannot_break_out_of_the_query(client_for):
    client = client_for("admin", [])

    response = client.get(
        "/api/activity/logs", params={"project": "shop", "search": "a` or `b"}
    )

    assert response.status_code == 400
