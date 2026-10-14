import asyncio
import json
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.release import ReleaseEvent
from app.routers import releases
from app.services import keycloak_service
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError
from app.services.release.source import RolloutSnapshot
from tests.test_release_view import FAST, REPLICA_SETS, _rollout, _run, _smoke_done, _weights

# The shape of a real user token of the realm: no ``sub`` claim (CMP 0.3.9).
USER_TOKENS = {
    "dev": {"preferred_username": "dev-alice", "realm_access": {"roles": []}},
    "guest": {"preferred_username": "guest-bob", "realm_access": {"roles": []}},
    "outsider": {"preferred_username": "eve", "realm_access": {"roles": []}},
}
KEYCLOAK_IDS = {"dev-alice": "uid-alice", "guest-bob": "uid-bob", "eve": "uid-eve"}
ROLES = {("uid-alice", "shop"): "member", ("uid-bob", "shop"): "guest"}


class FakeSource:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.patches = []

    def fetch(self, namespace, app_name):
        assert (namespace, app_name) == ("shop-web", "web")
        return self.snapshot

    def patch_status(self, namespace, rollout_name, body):
        self.patches.append((namespace, rollout_name, body))


def _paused():
    rollout = _rollout(
        currentStepIndex=6,
        phase="Paused",
        pauseConditions=[{"reason": "CanaryPauseStep", "startTime": "2026-10-14T14:09:30Z"}],
        canary=_weights(25),
    )
    return RolloutSnapshot(rollout=rollout, analysis_runs=[_smoke_done()], replica_sets=REPLICA_SETS)


def _analysing(**annotations):
    rollout = _rollout(steps=FAST, annotations=annotations, currentStepIndex=4, canary=_weights(10))
    return RolloutSnapshot(rollout=rollout, analysis_runs=[_run(4, "Running", started=10)], replica_sets=REPLICA_SETS)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(
            Deployment(
                id=7,
                name="web",
                template_id="k3s-gitops-app",
                project_id="shop",
                provider_type=ProviderType.KUBERNETES,
                status=DeploymentStatus.RUNNING,
            )
        )
        db.commit()
    return factory


@pytest.fixture
def keycloak():
    # Real get_user_id_from_token: the no-sub fallback resolves the username.
    with (
        mock.patch.object(keycloak_service, "_get_admin_token", return_value="admin"),
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


def _client(session_factory, source, who="dev"):
    app = FastAPI()
    app.include_router(releases.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: USER_TOKENS[who]
    app.dependency_overrides[releases.get_release_source] = lambda: source
    return TestClient(app)


class TestAuthorization:
    def test_guest_reads_the_release_with_a_token_without_sub(self, session_factory, keycloak):
        client = _client(session_factory, FakeSource(_paused()), "guest")

        response = client.get("/api/deployments/7/release")

        assert response.json()["currentStepIndex"] == 6

    def test_outsider_cannot_read_the_release(self, session_factory, keycloak):
        client = _client(session_factory, FakeSource(_paused()), "outsider")

        assert client.get("/api/deployments/7/release").status_code == 403

    def test_outsider_cannot_read_the_history(self, session_factory, keycloak):
        client = _client(session_factory, FakeSource(_paused()), "outsider")

        assert client.get("/api/deployments/7/releases").status_code == 403

    def test_outsider_cannot_open_the_stream(self, session_factory, keycloak):
        client = _client(session_factory, FakeSource(_paused()), "outsider")

        assert client.get("/api/deployments/7/release/stream").status_code == 403

    @pytest.mark.parametrize(
        "path, body",
        [
            ("promote", {"mode": "next"}),
            ("promote", {"mode": "full", "reason": "hotfix"}),
            ("skip", {"reason": "metrics down"}),
            ("abort", {}),
        ],
    )
    def test_guest_cannot_act_on_the_release(self, session_factory, keycloak, path, body):
        source = FakeSource(_paused())
        client = _client(session_factory, source, "guest")

        response = client.post(f"/api/deployments/7/release/{path}", json=body)

        assert (response.status_code, source.patches) == (403, [])


class TestActions:
    def test_continue_clears_the_pause_and_records_who(self, session_factory, keycloak):
        source = FakeSource(_paused())
        client = _client(session_factory, source)

        client.post("/api/deployments/7/release/promote", json={"mode": "next", "reason": None})

        with session_factory() as db:
            actors = db.query(ReleaseEvent).one().actors
        assert (source.patches, [(a["user"], a["action"]) for a in actors]) == (
            [("release-lab", "web", {"status": {"pauseConditions": None}})],
            [("dev-alice", "promote")],
        )

    def test_promote_all_before_the_end_needs_a_reason(self, session_factory, keycloak):
        source = FakeSource(_paused())
        client = _client(session_factory, source)

        response = client.post("/api/deployments/7/release/promote", json={"mode": "full", "reason": "  "})

        assert (response.status_code, source.patches) == (422, [])

    def test_promote_all_is_recorded_as_forced(self, session_factory, keycloak):
        source = FakeSource(_paused())
        client = _client(session_factory, source)

        client.post("/api/deployments/7/release/promote", json={"mode": "full", "reason": "hotfix"})

        with session_factory() as db:
            event = db.query(ReleaseEvent).one()
        assert (source.patches[0][2], event.forced, event.actors[0]["reason"]) == (
            {"status": {"promoteFull": True}},
            True,
            "hotfix",
        )

    def test_skip_needs_a_reason(self, session_factory, keycloak):
        source = FakeSource(_analysing())
        client = _client(session_factory, source)

        response = client.post("/api/deployments/7/release/skip", json={})

        assert (response.status_code, source.patches) == (422, [])

    def test_skip_moves_past_the_running_analysis(self, session_factory, keycloak):
        source = FakeSource(_analysing())
        client = _client(session_factory, source)

        client.post("/api/deployments/7/release/skip", json={"reason": "metrics down"})

        assert source.patches[0][2] == {"status": {"currentStepIndex": 5}}

    def test_skip_is_refused_when_the_chart_forbids_forcing(self, session_factory, keycloak):
        source = FakeSource(_analysing(**{"cnp.3istor.com/release-allow-force": "false"}))
        client = _client(session_factory, source)

        response = client.post("/api/deployments/7/release/skip", json={"reason": "metrics down"})

        assert (response.status_code, source.patches) == (403, [])

    def test_continue_is_refused_while_an_analysis_runs(self, session_factory, keycloak):
        source = FakeSource(_analysing())
        client = _client(session_factory, source)

        response = client.post("/api/deployments/7/release/promote", json={"mode": "next"})

        assert response.status_code == 409

    def test_abort_sets_the_abort_flag(self, session_factory, keycloak):
        source = FakeSource(_paused())
        client = _client(session_factory, source)

        client.post("/api/deployments/7/release/abort", json={"reason": "wrong image"})

        assert source.patches[0][2] == {"status": {"abort": True}}

    def test_retry_clears_the_abort_flag(self, session_factory, keycloak):
        source = FakeSource(RolloutSnapshot(rollout=_rollout(abort=True), replica_sets=REPLICA_SETS))
        client = _client(session_factory, source)

        client.post("/api/deployments/7/release/retry")

        assert source.patches[0][2] == {"status": {"abort": False}}

    def test_unreachable_cluster_is_a_503(self, session_factory, keycloak):
        source = mock.Mock(fetch=mock.Mock(side_effect=KubeUnavailableError("down")))
        client = _client(session_factory, source)

        assert client.get("/api/deployments/7/release").status_code == 503


def test_app_without_rollout_has_release_disabled(session_factory, keycloak):
    client = _client(session_factory, FakeSource(None))

    assert client.get("/api/deployments/7/release").json()["enabled"] is False


def test_history_lists_the_attempts(session_factory, keycloak):
    client = _client(session_factory, FakeSource(_paused()))
    client.get("/api/deployments/7/release")

    rows = client.get("/api/deployments/7/releases").json()

    assert [(r["revision"], r["outcome"], r["image"]) for r in rows] == [(5, "running", "ghcr.io/3-istor/web:1.4.0")]


class TestStream:
    def _frames(self, session_factory, source, polls):
        calls = iter([False] * polls + [True])

        async def is_disconnected():
            return next(calls)

        async def collect():
            return [f async for f in releases.view_stream(7, source, is_disconnected)]

        with (
            mock.patch.object(releases, "SessionLocal", session_factory),
            mock.patch.object(releases, "POLL_SECONDS", 0),
        ):
            return asyncio.run(collect())

    def test_emits_only_when_the_view_changes(self, session_factory):
        source = FakeSource(_paused())

        frames = self._frames(session_factory, source, polls=3)

        assert len(frames) == 1 and json.loads(frames[0].removeprefix("data: "))["phase"] == "Paused"

    def test_stops_when_the_client_disconnects(self, session_factory):
        source = mock.Mock(wraps=FakeSource(_paused()))

        self._frames(session_factory, source, polls=2)

        assert source.fetch.call_count == 2

    def test_a_cluster_error_is_a_comment_the_client_ignores(self, session_factory):
        source = mock.Mock(fetch=mock.Mock(side_effect=KubeUnavailableError("down")))

        frames = self._frames(session_factory, source, polls=1)

        assert frames[0].startswith(": ")

    def test_sends_no_buffering_headers(self, session_factory, keycloak):
        client = _client(session_factory, FakeSource(_paused()))
        with (
            mock.patch.object(releases, "SessionLocal", session_factory),
            mock.patch.object(releases, "view_stream", side_effect=_one_frame),
        ):
            response = client.get("/api/deployments/7/release/stream")

        assert (response.headers["x-accel-buffering"], response.headers["cache-control"]) == ("no", "no-cache")


async def _one_frame(*_):
    yield "data: {}\n\n"
