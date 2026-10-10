import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.routers import activity as activity_router
from app.services import activity_collector as collector
from app.services import activity_sources as src
from app.services import keycloak_service
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import session_factory  # noqa: F401

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
NS_PROJECTS = {"shop-system": "shop", "shop-web": "shop"}
PROJECTS = {"shop"}


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
    event = src.kubernetes_event(NS_PROJECTS, NOW, audit_line())

    assert (event.action, event.notable, event.project, event.app) == (
        "kubernetes.exec",
        True,
        "shop",
        "web",
    )


def test_a_namespace_outside_projects_belongs_to_the_platform():
    line = audit_line(
        objectRef={"resource": "pods", "namespace": "kube-system", "name": "x"}
    )

    assert src.kubernetes_event(NS_PROJECTS, NOW, line).project is None


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

    assert src.kubernetes_event(NS_PROJECTS, NOW, line) is None


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

    assert src.kubernetes_event(NS_PROJECTS, NOW, line).actor == "system:admin"


def test_plain_reads_are_flagged_as_reads():
    line = audit_line(
        verb="get",
        objectRef={"resource": "pods", "namespace": "shop-web", "name": "w"},
    )

    assert src.kubernetes_event(NS_PROJECTS, NOW, line).is_read


def test_secret_reads_by_a_person_are_notable_not_hidden():
    line = audit_line(
        verb="get",
        objectRef={
            "resource": "secrets",
            "namespace": "shop-web",
            "name": "db",
        },
    )

    event = src.kubernetes_event(NS_PROJECTS, NOW, line)

    assert (event.notable, event.is_read) == (True, False)


def test_vault_path_gives_the_project():
    line = json.dumps(
        {
            "auth": {"display_name": "oidc-alice"},
            "request": {
                "path": "project-shop/data/web/db",
                "operation": "read",
            },
        }
    )

    event = src.vault_event(PROJECTS, NOW, line)

    assert (event.project, event.app, event.target) == (
        "shop",
        "web",
        "web/db",
    )


def test_vault_path_of_an_unknown_project_is_not_attributed():
    line = json.dumps(
        {
            "auth": {"display_name": "oidc-alice"},
            "request": {
                "path": "project-shopping/data/web",
                "operation": "read",
            },
        }
    )

    assert src.vault_event(PROJECTS, NOW, line).project is None


def test_vault_reads_by_the_secrets_operator_are_skipped():
    line = json.dumps(
        {
            "auth": {
                "display_name": "kubernetes-vault-secrets-operator-vault-secrets-operator"
            },
            "request": {"path": "project-shop/data/web", "operation": "read"},
        }
    )

    assert src.vault_event(PROJECTS, NOW, line) is None


def test_keycloak_login_error_is_a_failure_of_the_project_realm():
    line = (
        '2026-10-10 WARN [org.keycloak.events] type="LOGIN_ERROR", realmId="1", '
        'realmName="shop", clientId="web", username="bob", ipAddress="1.2.3.4", '
        'error="invalid_user_credentials"'
    )

    event = src.keycloak_event(PROJECTS, NOW, line)

    assert (event.action, event.outcome, event.project) == (
        "keycloak.login_error",
        "failure",
        "shop",
    )


def test_logs_query_is_pinned_to_the_project():
    query = src.logs_query("shop", "shop-web", None, None, "timeout", "error")

    assert query.startswith(
        '{project="shop", namespace="shop-web"} |= `timeout`'
    )


def test_collector_resumes_after_a_full_page(session_factory):
    calls = []

    async def fake_lines(query, start, end, limit, direction="backward"):
        calls.append((start, end))
        if len(calls) == 1:
            return [
                (
                    start + timedelta(seconds=i),
                    {},
                    audit_line(requestURI=f"/{i}"),
                )
                for i in range(limit)
            ][::-1]
        return []

    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    with (
        mock.patch.object(collector, "SessionLocal", session_factory),
        mock.patch.object(src, "loki_lines", fake_lines),
        mock.patch.object(collector, "PAGE", 3),
        mock.patch.object(collector, "_cursor", lambda s: start),
    ):
        added = asyncio.run(
            collector.collect_source("kubernetes", NS_PROJECTS)
        )

    assert (added, calls[1][0]) == (
        3,
        start + timedelta(seconds=2, microseconds=1),
    )


def test_collector_stores_each_event_once(session_factory):
    event = src.kubernetes_event(NS_PROJECTS, NOW, audit_line())

    with mock.patch.object(collector, "SessionLocal", session_factory):
        collector.store([event])
        added = collector.store([event])

    assert added == 0


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
        with mock.patch.object(collector, "SessionLocal", session_factory):
            collector.store(events)
        patch = mock.patch.object(
            keycloak_service, "get_project_role", lambda u, p: role
        )
        patch.start()
        started.append(patch)
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


def flow_line(direction="INGRESS", port=8000) -> str:
    return json.dumps(
        {
            "flow": {
                "verdict": "DROPPED",
                "IP": {"source": "10.0.0.1", "destination": "10.0.0.2"},
                "l4": {
                    "TCP": {"source_port": 40000, "destination_port": port}
                },
                "source": {
                    "namespace": "default",
                    "pod_name": "probe-7d4f9c8b6d-x2k4p",
                },
                "destination": {
                    "namespace": "shop-web",
                    "pod_name": "web-5f7c9d8b4c-abcde",
                },
                "traffic_direction": direction,
                "drop_reason_desc": "POLICY_DENIED",
            }
        }
    )


def test_an_ingress_drop_belongs_to_the_destination_project():
    event = src.network_event(NS_PROJECTS, NOW, flow_line())

    assert (event.project, event.actor, event.target) == (
        "shop",
        "default/probe",
        "shop-web/web",
    )


def test_an_egress_drop_from_outside_projects_is_ignored():
    assert src.network_event(NS_PROJECTS, NOW, flow_line("EGRESS")) is None


def test_denied_flows_are_grouped_by_pair_and_port(client_for):
    flows = [
        src.network_event(
            NS_PROJECTS,
            datetime.now(timezone.utc) - timedelta(minutes=m),
            flow_line(),
        )
        for m in (1, 2, 3)
    ]
    client = client_for("member", flows)

    response = client.get("/api/activity/network?project=shop")

    assert [
        (f["destination"], f["port"], f["count"]) for f in response.json()
    ] == [("shop-web/web", 8000, 3)]


def test_summary_counts_realm_logins_for_admins(client_for):
    client = client_for(
        "admin",
        [
            event("keycloak", "bob", "keycloak.login_error", 10),
            event("keycloak", "bob", "keycloak.login_error", 20),
            event("keycloak", "alice", "keycloak.login", 30),
        ],
    )

    logins = client.get("/api/activity/summary?project=shop").json()["logins"]

    assert (
        sum(d["failure"] for d in logins["days"]),
        logins["top_failed_users"],
    ) == (2, [["bob", 2]])


def test_members_get_no_login_breakdown(client_for):
    client = client_for("member", [])

    summary = client.get("/api/activity/summary?project=shop").json()

    assert summary["logins"] is None


def test_platform_status_is_for_cnp_admins_only(client_for):
    client = client_for("admin", [])

    response = client.get("/api/activity/platform")

    assert response.status_code == 403


def test_log_volume_sums_each_namespace_and_finds_the_peak(client_for):
    client = client_for("member", [])
    t1 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(hours=1)

    async def matrix(query, start, end, step):
        value = 600.0 if "count_over_time" in query else 6000.0
        return [({"namespace": "shop-web"}, [(t1, value), (t2, value / 2)])]

    with mock.patch.object(src, "loki_matrix", matrix):
        body = client.get(
            "/api/activity/logs/volume?project=shop&hours=24"
        ).json()

    [series] = body["series"]
    assert (
        series["total_lines"],
        series["total_bytes"],
        series["peak_per_minute"],
    ) == (
        900,
        9000,
        600 / (body["step_seconds"] / 60),
    )


def test_log_volume_refuses_a_foreign_label_value(client_for):
    client = client_for("member", [])

    response = client.get(
        "/api/activity/logs/volume",
        params={"project": "shop", "namespace": 'x"}'},
    )

    assert response.status_code == 400
