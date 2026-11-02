import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.routers import app_overview
from app.services import activity_sources as sources
from app.services import app_overview as overview
from app.services import keycloak_service
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError

USER_TOKENS = {
    "dev": {"preferred_username": "dev-alice", "realm_access": {"roles": []}},
    "guest": {
        "preferred_username": "guest-bob",
        "realm_access": {"roles": []},
    },
    "outsider": {"preferred_username": "eve", "realm_access": {"roles": []}},
}
KEYCLOAK_IDS = {
    "dev-alice": "uid-alice",
    "guest-bob": "uid-bob",
    "eve": "uid-eve",
}
ROLES = {("uid-alice", "shop"): "member", ("uid-bob", "shop"): "guest"}

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
FRONTEND = "shop-web-frontend-cnp-generic-app"
BACKEND = "shop-web-backend-cnp-generic-app"
FULLSTACK = json.dumps({"app_type": "fullstack"})


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all(
            [
                Deployment(
                    id=7,
                    name="web",
                    template_id="k3s",
                    project_id="shop",
                    provider_type=ProviderType.KUBERNETES,
                    status=DeploymentStatus.RUNNING,
                    app_config=FULLSTACK,
                ),
                Deployment(
                    id=8,
                    name="secret-app",
                    template_id="k3s",
                    project_id="rival",
                    provider_type=ProviderType.KUBERNETES,
                    status=DeploymentStatus.RUNNING,
                ),
                Deployment(
                    id=9,
                    name="legacy",
                    template_id="aws",
                    project_id="shop",
                    provider_type=ProviderType.LEGACY_HYBRID,
                    status=DeploymentStatus.RUNNING,
                ),
            ]
        )
        db.commit()
    return factory


@pytest.fixture
def keycloak():
    with (
        mock.patch.object(
            keycloak_service, "_get_admin_token", return_value="admin"
        ),
        mock.patch.object(
            keycloak_service,
            "_find_user_by_username",
            side_effect=lambda username, _: {"id": KEYCLOAK_IDS[username]},
        ),
        mock.patch.object(
            keycloak_service,
            "get_project_role",
            side_effect=lambda user_id, project: ROLES.get((user_id, project)),
        ),
    ):
        yield


def _client(session_factory, who="dev"):
    app = FastAPI()
    app.include_router(app_overview.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: USER_TOKENS[who]
    return TestClient(app)


def _transport(monkeypatch, module, handler):
    sent = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    monkeypatch.setattr(
        module,
        "_client",
        lambda *args, **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            base_url="http://backend.test",
            headers={"X-Scope-OrgID": args[0]} if args and args[0] else {},
        ),
    )
    return sent


def _matrix(values):
    return httpx.Response(
        200,
        json={
            "status": "success",
            "data": {"resultType": "matrix", "result": values},
        },
    )


def _series(points):
    return {"metric": {}, "values": points}


class TestAccess:
    @pytest.mark.parametrize("path", ["metrics", "logs", "events"])
    def test_guest_cannot_read_app_overview_data(
        self, session_factory, keycloak, path
    ):
        client = _client(session_factory, "guest")

        assert client.get(f"/api/deployments/7/{path}").status_code == 403

    @pytest.mark.parametrize("path", ["metrics", "logs", "events"])
    def test_outsider_cannot_read_app_overview_data(
        self, session_factory, keycloak, path
    ):
        client = _client(session_factory, "outsider")

        assert client.get(f"/api/deployments/7/{path}").status_code == 403

    @pytest.mark.parametrize("path", ["metrics", "logs", "events"])
    def test_member_of_another_project_cannot_read_this_app(
        self, session_factory, keycloak, path
    ):
        client = _client(session_factory, "dev")

        assert client.get(f"/api/deployments/8/{path}").status_code == 403

    @pytest.mark.parametrize("path", ["metrics", "logs", "events"])
    def test_unknown_app_answers_404(self, session_factory, keycloak, path):
        client = _client(session_factory, "dev")

        assert client.get(f"/api/deployments/99/{path}").status_code == 404

    @pytest.mark.parametrize("path", ["metrics", "logs", "events"])
    def test_legacy_app_is_refused(self, session_factory, keycloak, path):
        client = _client(session_factory, "dev")

        assert client.get(f"/api/deployments/9/{path}").status_code == 400


class TestMetrics:
    def _handler(self, total=None, errors=None, cpu=None, request=None):
        def handle(req: httpx.Request) -> httpx.Response:
            query = req.url.params["query"]
            if req.url.path.endswith("/query"):
                return _matrix(
                    [{"metric": {}, "value": [1, str(request)]}]
                    if request is not None
                    else []
                )
            if "rq_total" in query:
                return _matrix(total or [])
            if "rq_xx" in query:
                return _matrix(errors or [])
            return _matrix(cpu or [])

        return handle

    def test_returns_sparkline_points_for_each_metric(
        self, session_factory, keycloak, monkeypatch
    ):
        _transport(
            monkeypatch,
            overview,
            self._handler(
                total=[_series([[1000, "10"], [1900, "20"]])],
                errors=[_series([[1000, "1"], [1900, "1"]])],
                cpu=[_series([[1000, "40"], [1900, "55.5"]])],
                request=100,
            ),
        )
        client = _client(session_factory)

        body = client.get("/api/deployments/7/metrics").json()

        assert (
            [p["value"] for p in body["requests_per_second"]["points"]],
            [p["value"] for p in body["errors_5xx_percent"]["points"]],
            body["cpu_millicores"]["current"],
            body["cpu_request_millicores"],
        ) == ([10, 20], [10, 5], 55.5, 100)

    def test_traffic_is_null_when_the_app_has_no_ingress_metrics(
        self, session_factory, keycloak, monkeypatch
    ):
        _transport(
            monkeypatch,
            overview,
            self._handler(cpu=[_series([[1000, "40"]])], request=100),
        )
        client = _client(session_factory)

        body = client.get("/api/deployments/7/metrics").json()

        assert (
            body["requests_per_second"],
            body["errors_5xx_percent"],
            body["cpu_millicores"]["current"],
        ) == (None, None, 40)

    def test_error_share_is_zero_when_traffic_has_no_5xx_series(
        self, session_factory, keycloak, monkeypatch
    ):
        _transport(
            monkeypatch,
            overview,
            self._handler(total=[_series([[1000, "4"]])]),
        )
        client = _client(session_factory)

        body = client.get("/api/deployments/7/metrics").json()

        assert body["errors_5xx_percent"]["current"] == 0

    def test_queries_stay_on_the_apps_namespace_and_onprem_cluster(
        self, session_factory, keycloak, monkeypatch
    ):
        sent = _transport(monkeypatch, overview, self._handler())
        client = _client(session_factory)

        client.get("/api/deployments/7/metrics")

        assert {
            (
                "shop-web" in r.url.params["query"],
                r.url.params["extra_filters[]"],
            )
            for r in sent
        } == {(True, '{cluster=~"onprem|"}')}

    def test_returns_502_when_victoria_metrics_is_down(
        self, session_factory, keycloak, monkeypatch
    ):
        def down(_: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        _transport(monkeypatch, overview, down)
        client = _client(session_factory)

        assert client.get("/api/deployments/7/metrics").status_code == 502

    def test_rejects_an_unknown_window(self, session_factory, keycloak):
        client = _client(session_factory)

        response = client.get("/api/deployments/7/metrics?window=90d")

        assert response.status_code == 422


class TestLogs:
    def _loki(self, lines):
        def handle(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "result": [
                            {
                                "stream": {"pod": f"{FRONTEND}-abc-xyz"},
                                "values": [
                                    [str(int(NOW.timestamp() * 1e9)), line]
                                    for line in lines
                                ],
                            }
                        ]
                    }
                },
            )

        return handle

    def test_reads_only_the_projects_own_loki_tenant(
        self, session_factory, keycloak, monkeypatch
    ):
        sent = _transport(monkeypatch, sources, self._loki(["hello"]))
        client = _client(session_factory)

        client.get("/api/deployments/7/logs")

        request = sent[0]
        assert (
            request.url.params["query"].startswith(
                '{project="shop", namespace="shop-web"'
            ),
            request.headers.get("X-Scope-OrgID"),
        ) == (True, "shop|default")

    def test_another_projects_logs_are_never_queried(
        self, session_factory, keycloak, monkeypatch
    ):
        sent = _transport(monkeypatch, sources, self._loki(["hello"]))
        client = _client(session_factory)

        response = client.get("/api/deployments/8/logs")

        assert (response.status_code, sent) == (403, [])

    def test_a_caller_cannot_inject_another_project_through_the_filters(
        self, session_factory, keycloak, monkeypatch
    ):
        sent = _transport(monkeypatch, sources, self._loki([]))
        client = _client(session_factory)

        client.get(
            "/api/deployments/7/logs",
            params={"q": 'x", project="rival', "component": "backend"},
        )

        selector = sent[0].url.params["query"].split("}")[0]
        assert (selector.count("project="), "rival" in selector) == (1, False)

    def test_labels_each_line_with_its_level_as_a_word(
        self, session_factory, keycloak, monkeypatch
    ):
        _transport(
            monkeypatch,
            sources,
            self._loki(
                ["ERROR db down", "level=warn slow", "started", "<b>x</b>"]
            ),
        )
        client = _client(session_factory)

        body = client.get("/api/deployments/7/logs").json()

        assert [line["level"] for line in body] == [
            "error",
            "warn",
            None,
            None,
        ]

    def test_returns_lines_as_plain_text_without_interpreting_them(
        self, session_factory, keycloak, monkeypatch
    ):
        _transport(monkeypatch, sources, self._loki(["<script>x</script>"]))
        client = _client(session_factory)

        body = client.get("/api/deployments/7/logs").json()

        assert body[0]["line"] == "<script>x</script>"

    def test_filters_by_component_with_the_charts_pod_prefix(
        self, session_factory, keycloak, monkeypatch
    ):
        sent = _transport(monkeypatch, sources, self._loki([]))
        client = _client(session_factory)

        client.get("/api/deployments/7/logs?component=backend")

        assert f'pod=~"{BACKEND}-.+"' in sent[0].url.params["query"]

    def test_attaches_the_component_of_the_pod(
        self, session_factory, keycloak, monkeypatch
    ):
        _transport(monkeypatch, sources, self._loki(["hello"]))
        client = _client(session_factory)

        body = client.get("/api/deployments/7/logs").json()

        assert body[0]["component"] == "frontend"

    def test_filters_by_level_in_the_query(
        self, session_factory, keycloak, monkeypatch
    ):
        sent = _transport(monkeypatch, sources, self._loki([]))
        client = _client(session_factory)

        client.get("/api/deployments/7/logs?level=error")

        assert "|~" in sent[0].url.params["query"]

    def test_rejects_an_unknown_component(self, session_factory, keycloak):
        client = _client(session_factory)

        response = client.get("/api/deployments/7/logs?component=db")

        assert response.status_code == 400

    def test_rejects_a_backtick_in_the_search_text(
        self, session_factory, keycloak
    ):
        client = _client(session_factory)

        response = client.get("/api/deployments/7/logs", params={"q": "a`b"})

        assert response.status_code == 400

    def test_rejects_more_lines_than_the_cap(self, session_factory, keycloak):
        client = _client(session_factory)

        response = client.get("/api/deployments/7/logs?limit=5000")

        assert response.status_code == 422

    def test_returns_502_when_loki_is_down(
        self, session_factory, keycloak, monkeypatch
    ):
        def down(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        _transport(monkeypatch, sources, down)
        client = _client(session_factory)

        assert client.get("/api/deployments/7/logs").status_code == 502


def _event(kind, name, reason, minutes_ago, message="m", count=1):
    when = (NOW - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "metadata": {"creationTimestamp": when},
        "involvedObject": {"kind": kind, "name": name},
        "reason": reason,
        "message": message,
        "type": "Normal",
        "lastTimestamp": when,
        "count": count,
    }


class TestEvents:
    def test_keeps_only_events_of_the_apps_workloads_newest_first(
        self, session_factory, keycloak
    ):
        items = [
            _event("Pod", f"{FRONTEND}-abc-xyz", "Pulled", 30),
            _event("ReplicaSet", f"{BACKEND}-abc", "SuccessfulCreate", 5),
            _event("Rollout", BACKEND, "RolloutUpdated", 10),
            _event("Pod", "shop-web-backend-db-1", "Pulled", 1),
            _event("Pod", "shop-other-frontend-cnp-generic-app-a-b", "X", 2),
            _event("Service", FRONTEND, "Synced", 3),
        ]
        client = _client(session_factory)

        with mock.patch.object(overview, "kube_list", return_value=items):
            body = client.get("/api/deployments/7/events").json()

        assert [e["reason"] for e in body] == [
            "SuccessfulCreate",
            "RolloutUpdated",
            "Pulled",
        ]

    def test_reads_the_prod_namespace_of_the_app(
        self, session_factory, keycloak
    ):
        client = _client(session_factory)

        with mock.patch.object(
            overview, "kube_list", return_value=[]
        ) as kube_list:
            client.get("/api/deployments/7/events")

        assert (
            kube_list.call_args.args[0] == "/api/v1/namespaces/shop-web/events"
        )

    def test_honours_the_limit(self, session_factory, keycloak):
        items = [
            _event("Pod", f"{FRONTEND}-a-b", f"R{i}", i) for i in range(5)
        ]
        client = _client(session_factory)

        with mock.patch.object(overview, "kube_list", return_value=items):
            body = client.get("/api/deployments/7/events?limit=2").json()

        assert len(body) == 2

    def test_returns_502_when_the_cluster_refuses_the_read(
        self, session_factory, keycloak
    ):
        client = _client(session_factory)

        with mock.patch.object(
            overview, "kube_list", side_effect=KubeUnavailableError("403")
        ):
            response = client.get("/api/deployments/7/events")

        assert response.status_code == 502
