import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.environment import AppEnvironment, Promotion
from app.models.release import ReleaseEvent
from app.routers import releases
from app.services import audit, keycloak_service
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError
from app.services.release.source import RolloutSnapshot
from tests.test_release_view import (
    FAST,
    REPLICA_SETS,
    _rollout,
    _run,
    _smoke_done,
    _weights,
)

# The shape of a real user token of the realm: no ``sub`` claim (CMP 0.3.9).
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


class FakeSource:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.patches = []

    def fetch(self, namespace, app_name, cloud):
        assert (namespace, app_name) == ("shop-web", "web")
        return self.snapshot

    def patch_status(self, namespace, rollout_name, body, cloud):
        self.patches.append((namespace, rollout_name, body))


def _paused():
    rollout = _rollout(
        currentStepIndex=6,
        phase="Paused",
        pauseConditions=[
            {"reason": "CanaryPauseStep", "startTime": "2026-10-14T14:09:30Z"}
        ],
        canary=_weights(25),
    )
    return RolloutSnapshot(
        rollout=rollout,
        analysis_runs=[_smoke_done()],
        replica_sets=REPLICA_SETS,
    )


def _analysing(**annotations):
    rollout = _rollout(
        steps=FAST,
        annotations=annotations,
        currentStepIndex=4,
        canary=_weights(10),
    )
    return RolloutSnapshot(
        rollout=rollout,
        analysis_runs=[_run(4, "Running", started=10)],
        replica_sets=REPLICA_SETS,
    )


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
    def test_guest_reads_the_release_with_a_token_without_sub(
        self, session_factory, keycloak
    ):
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

        assert (
            client.get("/api/deployments/7/release/stream").status_code == 403
        )

    @pytest.mark.parametrize(
        "path, body",
        [
            ("promote", {"mode": "next"}),
            ("promote", {"mode": "full", "reason": "hotfix"}),
            ("skip", {"reason": "metrics down"}),
            ("abort", {}),
        ],
    )
    def test_guest_cannot_act_on_the_release(
        self, session_factory, keycloak, path, body
    ):
        source = FakeSource(_paused())
        client = _client(session_factory, source, "guest")

        response = client.post(f"/api/deployments/7/release/{path}", json=body)

        assert (response.status_code, source.patches) == (403, [])


class TestActions:
    def test_continue_moves_past_the_pause_and_records_who(
        self, session_factory, keycloak
    ):
        source = FakeSource(_paused())
        client = _client(session_factory, source)

        client.post(
            "/api/deployments/7/release/promote",
            json={"mode": "next", "reason": None},
        )

        with session_factory() as db:
            actors = db.query(ReleaseEvent).one().actors
        assert (
            source.patches,
            [(a["user"], a["action"]) for a in actors],
        ) == (
            [
                (
                    "release-lab",
                    "web",
                    {
                        "status": {
                            "pauseConditions": None,
                            "controllerPause": False,
                            "currentStepIndex": 7,
                        }
                    },
                )
            ],
            [("dev-alice", "promote")],
        )

    def test_promote_all_before_the_end_needs_a_reason(
        self, session_factory, keycloak
    ):
        source = FakeSource(_paused())
        client = _client(session_factory, source)

        response = client.post(
            "/api/deployments/7/release/promote",
            json={"mode": "full", "reason": "  "},
        )

        assert (response.status_code, source.patches) == (422, [])

    def test_promote_all_is_recorded_as_forced(
        self, session_factory, keycloak
    ):
        source = FakeSource(_paused())
        client = _client(session_factory, source)

        client.post(
            "/api/deployments/7/release/promote",
            json={"mode": "full", "reason": "hotfix"},
        )

        with session_factory() as db:
            event = db.query(ReleaseEvent).one()
        assert (
            source.patches[0][2],
            event.forced,
            event.actors[0]["reason"],
        ) == (
            {"status": {"promoteFull": True}},
            True,
            "hotfix",
        )

    def test_skip_needs_a_reason(self, session_factory, keycloak):
        source = FakeSource(_analysing())
        client = _client(session_factory, source)

        response = client.post("/api/deployments/7/release/skip", json={})

        assert (response.status_code, source.patches) == (422, [])

    def test_skip_moves_past_the_running_analysis(
        self, session_factory, keycloak
    ):
        source = FakeSource(_analysing())
        client = _client(session_factory, source)

        client.post(
            "/api/deployments/7/release/skip", json={"reason": "metrics down"}
        )

        assert source.patches[0][2] == {"status": {"currentStepIndex": 5}}

    def test_skip_is_refused_when_the_chart_forbids_forcing(
        self, session_factory, keycloak
    ):
        source = FakeSource(
            _analysing(**{"cnp.3istor.com/release-allow-force": "false"})
        )
        client = _client(session_factory, source)

        response = client.post(
            "/api/deployments/7/release/skip", json={"reason": "metrics down"}
        )

        assert (response.status_code, source.patches) == (403, [])

    def test_continue_is_refused_while_an_analysis_runs(
        self, session_factory, keycloak
    ):
        source = FakeSource(_analysing())
        client = _client(session_factory, source)

        response = client.post(
            "/api/deployments/7/release/promote", json={"mode": "next"}
        )

        assert response.status_code == 409

    def test_abort_sets_the_abort_flag(self, session_factory, keycloak):
        source = FakeSource(_paused())
        client = _client(session_factory, source)

        client.post(
            "/api/deployments/7/release/abort", json={"reason": "wrong image"}
        )

        assert source.patches[0][2] == {"status": {"abort": True}}

    def test_retry_clears_the_abort_flag(self, session_factory, keycloak):
        source = FakeSource(
            RolloutSnapshot(
                rollout=_rollout(abort=True), replica_sets=REPLICA_SETS
            )
        )
        client = _client(session_factory, source)

        client.post("/api/deployments/7/release/retry")

        assert source.patches[0][2] == {"status": {"abort": False}}

    def test_unreachable_cluster_is_a_503(self, session_factory, keycloak):
        source = mock.Mock(
            fetch=mock.Mock(side_effect=KubeUnavailableError("down"))
        )
        client = _client(session_factory, source)

        assert client.get("/api/deployments/7/release").status_code == 503


def test_app_without_rollout_has_release_disabled(session_factory, keycloak):
    client = _client(session_factory, FakeSource(None))

    assert client.get("/api/deployments/7/release").json()["enabled"] is False


def test_history_lists_the_attempts(session_factory, keycloak):
    client = _client(session_factory, FakeSource(_paused()))
    client.get("/api/deployments/7/release")

    rows = client.get("/api/deployments/7/releases").json()

    assert [(r["revision"], r["outcome"], r["image"]) for r in rows] == [
        (5, "running", "ghcr.io/3-istor/web:1.4.0")
    ]


class TestHistoryFilters:
    @pytest.fixture
    def client(self, session_factory, keycloak):
        with session_factory() as db:
            for environment, component, tag in [
                ("prod", "backend", "1.2.0"),
                ("prod", "frontend", "1.2.0"),
                ("staging", "backend", "1.2.1-RC.4"),
                ("staging", "frontend", "1.2.1_rc.5"),
            ]:
                db.add(
                    ReleaseEvent(
                        deployment_id=7,
                        revision=1,
                        image=f"ghcr.io/3-istor/web/{component}:{tag}",
                        strategy="rolling",
                        started_at=datetime.now(timezone.utc),
                        outcome="deployed",
                        forced=False,
                        environment=environment,
                        component=component,
                    )
                )
            db.commit()
        return _client(session_factory, FakeSource(None))

    @staticmethod
    def _tags(client, **params):
        rows = client.get("/api/deployments/7/releases", params=params).json()
        return sorted(
            (r["environment"], r["component"], r["image"].rsplit(":", 1)[1])
            for r in rows
        )

    def test_environment_and_component_combine(self, client):
        assert self._tags(client, environment="prod", component="backend") == [
            ("prod", "backend", "1.2.0")
        ]

    def test_version_is_a_case_insensitive_substring(self, client):
        assert self._tags(client, version="rc.4") == [
            ("staging", "backend", "1.2.1-RC.4")
        ]

    def test_version_wildcards_are_literal(self, client):
        # "_" must not match any character as in SQL LIKE.
        assert self._tags(client, version="1_rc") == [
            ("staging", "frontend", "1.2.1_rc.5")
        ]

    def test_injection_payloads_are_plain_values(self, client):
        for params in (
            {"component": "x' OR '1'='1"},
            {"version": "'; DROP TABLE release_events; --"},
        ):
            assert self._tags(client, **params) == []
        assert len(self._tags(client)) == 4

    def test_limit_keeps_the_newest(self, client):
        rows = client.get(
            "/api/deployments/7/releases", params={"limit": 1}
        ).json()
        assert [r["image"].rsplit(":", 1)[1] for r in rows] == ["1.2.1_rc.5"]

    def test_unknown_environment_is_refused(self, client):
        response = client.get(
            "/api/deployments/7/releases", params={"environment": "dev"}
        )
        assert response.status_code == 422


DIGEST = "sha256:" + "a" * 64
LIVE = "sha256:" + "b" * 64


def _rolling_row(
    db, environment="prod", image=f"ghcr.io/3-istor/web:1.4.0@{DIGEST}"
):
    event = ReleaseEvent(
        deployment_id=7,
        revision=3,
        image=image,
        strategy="rolling",
        started_at=datetime.now(timezone.utc),
        outcome="deployed",
        forced=False,
        environment=environment,
        component="backend",
    )
    db.add(event)
    db.commit()
    return event.id


def _promotion(db, status="done", digest=DIGEST, target_env="prod"):
    promotion = Promotion(
        deployment_id=7,
        source_env="staging",
        target_env=target_env,
        digests={"backend": f"ghcr.io/3-istor/web@{digest}"},
        status=status,
        actor="dev",
        created_at=datetime.now(timezone.utc),
    )
    db.add(promotion)
    db.commit()
    return promotion.id


def _with_staging(db):
    db.add(
        AppEnvironment(
            deployment_id=7,
            name="staging",
            state="ready",
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _rollback_ids(session_factory):
    client = _client(session_factory, FakeSource(None))
    rows = client.get("/api/deployments/7/releases").json()
    return [r["rollback_promotion_id"] for r in rows]


class TestRollbackTarget:
    def test_a_prod_row_from_a_promotion_offers_a_rollback(
        self, session_factory, keycloak
    ):
        with session_factory() as db:
            _with_staging(db)
            _promotion(db)  # older one with the same digest
            newest = _promotion(db)
            _promotion(db, digest=LIVE)  # what prod runs now
            _rolling_row(db)

        assert _rollback_ids(session_factory) == [newest]

    def test_the_version_prod_runs_offers_none(
        self, session_factory, keycloak
    ):
        with session_factory() as db:
            _with_staging(db)
            _promotion(db)
            _promotion(db, digest=LIVE)
            _rolling_row(db)
            _rolling_row(db, image=f"ghcr.io/3-istor/web:1.5.0@{LIVE}")

        # Newest row first: the live one has no target, the older one has.
        assert _rollback_ids(session_factory) == [None, 1]

    def test_a_staging_row_offers_none(self, session_factory, keycloak):
        with session_factory() as db:
            _with_staging(db)
            _promotion(db)
            _rolling_row(db, environment="staging")

        assert _rollback_ids(session_factory) == [None]

    def test_an_app_without_staging_offers_none(
        self, session_factory, keycloak
    ):
        with session_factory() as db:
            _promotion(db)
            _rolling_row(db)

        assert _rollback_ids(session_factory) == [None]

    def test_a_failed_or_cancelled_promotion_offers_none(
        self, session_factory, keycloak
    ):
        with session_factory() as db:
            _with_staging(db)
            _promotion(db, status="failed")
            _promotion(db, status="cancelled")
            _promotion(db, target_env="staging")
            _rolling_row(db)

        assert _rollback_ids(session_factory) == [None]


class TestStream:
    @pytest.fixture(autouse=True)
    def fast_clock(self, session_factory):
        with (
            mock.patch.object(releases, "SessionLocal", session_factory),
            mock.patch.object(releases, "POLL_SECONDS", 0.01),
            mock.patch.object(releases, "KEEPALIVE_SECONDS", 0.05),
        ):
            yield

    def _frames(self, source, checks):
        calls = iter([False] * checks + [True])

        async def is_disconnected():
            return next(calls)

        async def collect():
            return [
                f
                async for f in releases.view_stream(7, source, is_disconnected)
            ]

        return asyncio.run(collect())

    def test_emits_only_when_the_view_changes(self):
        frames = self._frames(FakeSource(_paused()), checks=4)

        views = [f for f in frames if f.startswith("data: ")]
        assert (
            len(views) == 1
            and json.loads(views[0].removeprefix("data: "))["phase"]
            == "Paused"
        )

    def test_a_cluster_error_is_a_comment_the_client_ignores(self):
        source = mock.Mock(
            fetch=mock.Mock(side_effect=KubeUnavailableError("down"))
        )

        frames = self._frames(source, checks=2)

        assert frames[0].startswith(": ") and "down" in frames[0]

    def test_open_streams_share_one_poller(self):
        source = mock.Mock(wraps=FakeSource(_paused()))

        async def scenario():
            async def tab():
                async def is_disconnected():
                    return asyncio.get_running_loop().time() > deadline

                return [
                    f
                    async for f in releases.view_stream(
                        7, source, is_disconnected
                    )
                ]

            deadline = asyncio.get_running_loop().time() + 0.2
            await asyncio.gather(*(tab() for _ in range(10)))

        asyncio.run(scenario())

        # One poller at 10 ms for 200 ms, not ten of them.
        assert source.fetch.call_count <= 30

    def test_polling_stops_when_the_last_stream_leaves(self):
        source = mock.Mock(wraps=FakeSource(_paused()))

        async def scenario():
            self_disconnected = iter([False, False, True])

            async def is_disconnected():
                return next(self_disconnected)

            [_ async for _ in releases.view_stream(7, source, is_disconnected)]
            await asyncio.sleep(0.05)
            after_leave = source.fetch.call_count
            await asyncio.sleep(0.1)
            return after_leave

        after_leave = asyncio.run(scenario())

        assert (source.fetch.call_count, releases._watches) == (
            after_leave,
            {},
        )

    def test_a_closed_stream_stops_polling_behind_the_audit_middleware(
        self, session_factory, keycloak
    ):
        # The 0.3.34 leak: BaseHTTPMiddleware hid the disconnect, every closed
        # tab kept polling the cluster.
        source = mock.Mock(wraps=FakeSource(_paused()))
        app = _client(session_factory, source).app
        app.add_middleware(audit.AuditMiddleware)

        async def scenario():
            got_frame, closed = asyncio.Event(), asyncio.Event()
            requested = False

            async def receive():
                nonlocal requested
                if not requested:
                    requested = True
                    return {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
                await closed.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message.get("body", b"").startswith(b"data:"):
                    got_frame.set()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/api/deployments/7/release/stream",
                "raw_path": b"/api/deployments/7/release/stream",
                "query_string": b"",
                "root_path": "",
                "headers": [],
                "client": ("127.0.0.1", 1),
                "server": ("test", 80),
            }
            response = asyncio.create_task(app(scope, receive, send))
            await asyncio.wait_for(got_frame.wait(), 2)
            closed.set()
            await asyncio.wait_for(response, 2)
            await asyncio.sleep(0.05)
            return source.fetch.call_count

        after_close = asyncio.run(scenario())

        assert (source.fetch.call_count, releases._watches) == (
            after_close,
            {},
        )

    def test_sends_no_buffering_headers(self, keycloak, session_factory):
        client = _client(session_factory, FakeSource(_paused()))
        with mock.patch.object(
            releases, "view_stream", side_effect=_one_frame
        ):
            response = client.get("/api/deployments/7/release/stream")

        assert (
            response.headers["x-accel-buffering"],
            response.headers["cache-control"],
        ) == ("no", "no-cache")


async def _one_frame(*_):
    yield "data: {}\n\n"


@pytest.fixture
def github(session_factory):
    """A linked repo whose tags and main head the test controls."""
    with session_factory() as db:
        db.get(Deployment, 7).github_repo_url = (
            "https://github.com/3-istor/web"
        )
        db.commit()
    fake = SimpleNamespace(
        list_tags=mock.AsyncMock(return_value=["v1.0.0", "v1.1.0-rc.1"]),
        head_commit=mock.AsyncMock(return_value=("abc123", "fix: x")),
        create_tag=mock.AsyncMock(return_value="abc123"),
    )
    with (
        mock.patch.object(
            releases.gitops, "token_for", mock.AsyncMock(return_value="gh")
        ),
        mock.patch.multiple(
            releases.repo_ops,
            list_tags=fake.list_tags,
            head_commit=fake.head_commit,
            create_tag=fake.create_tag,
        ),
    ):
        yield fake


@pytest.fixture
def staged(session_factory):
    with session_factory() as db:
        db.add(
            AppEnvironment(
                deployment_id=7,
                name="staging",
                state="ready",
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()


class TestCutRelease:
    def test_choices_are_release_candidates_when_staging_is_ready(
        self, session_factory, keycloak, github, staged
    ):
        client = _client(session_factory, FakeSource(None))

        response = client.get("/api/deployments/7/release/choices")

        assert response.json() == {
            "commitSha": "abc123",
            "commitMessage": "fix: x",
            "staged": True,
            "choices": [
                {"kind": "candidate", "tag": "v1.1.0-rc.2"},
                {"kind": "patch", "tag": "v1.1.1-rc.1"},
                {"kind": "minor", "tag": "v1.2.0-rc.1"},
                {"kind": "major", "tag": "v2.0.0-rc.1"},
            ],
        }

    def test_choices_are_final_versions_without_staging(
        self, session_factory, keycloak, github
    ):
        client = _client(session_factory, FakeSource(None))

        body = client.get("/api/deployments/7/release/choices").json()

        assert (body["staged"], [c["tag"] for c in body["choices"]]) == (
            False,
            ["v1.1.1", "v1.2.0", "v2.0.0"],
        )

    def test_the_tag_is_created_on_the_commit_the_user_saw(
        self, session_factory, keycloak, github, staged
    ):
        client = _client(session_factory, FakeSource(None))

        response = client.post(
            "/api/deployments/7/release/tags",
            json={"tag": "v1.1.0-rc.2", "sha": "abc123"},
        )

        assert (response.status_code, response.json()) == (
            201,
            {
                "tag": "v1.1.0-rc.2",
                "sha": "abc123",
                "runsUrl": "https://github.com/3-istor/web/actions",
            },
        )
        github.create_tag.assert_awaited_once_with(
            "gh", "3-istor/web", "v1.1.0-rc.2", sha="abc123"
        )

    def test_a_tag_that_is_no_longer_offered_is_refused(
        self, session_factory, keycloak, github, staged
    ):
        client = _client(session_factory, FakeSource(None))

        response = client.post(
            "/api/deployments/7/release/tags",
            json={"tag": "v1.1.0-rc.1", "sha": "abc123"},
        )

        assert response.status_code == 409
        github.create_tag.assert_not_awaited()

    def test_a_moved_main_is_refused(
        self, session_factory, keycloak, github, staged
    ):
        github.head_commit.return_value = ("def456", "feat: y")
        client = _client(session_factory, FakeSource(None))

        response = client.post(
            "/api/deployments/7/release/tags",
            json={"tag": "v1.1.0-rc.2", "sha": "abc123"},
        )

        assert response.status_code == 409
        assert "main moved" in response.json()["detail"]
        github.create_tag.assert_not_awaited()

    def test_a_down_cluster_refuses_the_tag(
        self, session_factory, keycloak, github, staged
    ):
        client = _client(session_factory, FakeSource(None))
        down = HTTPException(status_code=409, detail="The AWS cluster is down")

        with mock.patch.object(
            releases, "ensure_cluster_up", mock.AsyncMock(side_effect=down)
        ):
            response = client.post(
                "/api/deployments/7/release/tags",
                json={"tag": "v1.1.0-rc.2", "sha": "abc123"},
            )

        assert response.status_code == 409
        github.create_tag.assert_not_awaited()

    def test_a_guest_cannot_read_the_choices_nor_tag(
        self, session_factory, keycloak, github, staged
    ):
        client = _client(session_factory, FakeSource(None), "guest")

        read = client.get("/api/deployments/7/release/choices")
        cut = client.post(
            "/api/deployments/7/release/tags",
            json={"tag": "v1.1.0-rc.2", "sha": "abc123"},
        )

        assert (read.status_code, cut.status_code) == (403, 403)
        github.create_tag.assert_not_awaited()
