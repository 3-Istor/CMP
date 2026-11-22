import asyncio
import time
from datetime import datetime, timezone
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.models.project import Project, TargetCloud
from app.models.project_move import ProjectMove
from app.routers import moves
from app.services import keycloak_service
from app.services.audit import NOTABLE, match_action
from app.services.keycloak_service import get_current_user
from app.services.moves import loop, steps

ROLES = {"admin": "admin", "member": "member"}
NOW = time.time()


def _token(sub, **extra):
    return {
        "sub": sub,
        "preferred_username": sub,
        "acr": "gold",
        "auth_time": NOW,
        **extra,
    }


@pytest.fixture
def env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Project(
            project_name="alpha",
            owner_username="o",
            target_cloud=TargetCloud.AWS,
        )
    )
    session.commit()
    app = FastAPI()
    app.include_router(moves.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: session
    current = {"token": _token("admin")}
    app.dependency_overrides[get_current_user] = lambda: current["token"]
    with (
        mock.patch.object(settings, "STEP_UP_ENABLED", True),
        mock.patch.object(settings, "STEP_UP_ACR", "gold"),
        mock.patch.object(
            keycloak_service,
            "get_project_role",
            side_effect=lambda user, _project: ROLES.get(user),
        ),
        mock.patch.object(steps, "fresh_state", return_value="up"),
        mock.patch.object(
            steps.project_registry,
            "is_deletion_protected",
            new=mock.AsyncMock(return_value=False),
        ),
        mock.patch.object(steps, "still_labelled", return_value=False),
        mock.patch.object(steps, "_unrestored", return_value=[]),
    ):
        yield TestClient(app), current, session
    session.close()


def _start(http):
    return http.post("/api/projects/alpha/move", json={})


class TestStart:
    def test_member_is_refused(self, env):
        http, current, _ = env
        current["token"] = _token("member")

        assert _start(http).status_code == 403

    def test_admin_without_step_up_gets_the_challenge(self, env):
        http, current, _ = env
        current["token"] = _token("admin", acr="silver")

        response = _start(http)

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "step_up_required"

    def test_agent_token_is_refused(self, env):
        http, current, _ = env
        current["token"] = _token("admin", cnp_agent={})

        assert _start(http).status_code == 403

    @pytest.mark.parametrize(
        "path", ["/move", "/moves/1/retry", "/moves/1/cancel"]
    )
    def test_an_assistant_is_refused_even_without_step_up(self, env, path):
        http, current, _ = env
        current["token"] = _token("admin", cnp_agent={"client": "claude"})

        with mock.patch.object(settings, "STEP_UP_ENABLED", False):
            response = http.post(f"/api/projects/alpha{path}", json={})

        assert response.status_code == 403

    def test_the_data_that_starts_empty_is_listed(self, env):
        http, _, _ = env

        with mock.patch.object(
            steps, "_unrestored", return_value=["shop (staging)"]
        ):
            response = http.get("/api/projects/alpha/moves/data-loss")

        assert response.json() == ["shop (staging)"]

    def test_start_move_passes_target(self, env):
        http, _, _ = env

        with mock.patch.object(
            steps, "start_checks", new=mock.AsyncMock(return_value="onprem")
        ) as checks:
            response = http.post(
                "/api/projects/alpha/move", json={"target": "onprem"}
            )
            invalid = http.post(
                "/api/projects/alpha/move", json={"target": "aws"}
            )

        assert response.status_code == 202
        assert checks.await_args.args[3] == "onprem"
        assert invalid.status_code == 422

    def test_admin_starts_one_move_and_a_second_is_refused(self, env):
        http, _, db = env

        first = _start(http)
        second = _start(http)

        assert first.status_code == 202
        assert first.json()["target"] == "gcp"
        assert second.status_code == 409
        assert db.query(ProjectMove).count() == 1
        current = http.get("/api/projects/alpha/moves/current")
        assert current.json()["id"] == first.json()["id"]
        assert len(http.get("/api/projects/alpha/moves").json()) == 1

    def test_two_concurrent_starts_make_one_row(self, env):
        _, _, db = env
        ctx = mock.Mock(project_name="alpha", token=_token("admin"))
        ctx.username = "admin"

        async def both():
            return await asyncio.gather(
                moves.start_move(moves.MoveStart(), ctx, db),
                moves.start_move(moves.MoveStart(), ctx, db),
                return_exceptions=True,
            )

        results = asyncio.run(both())

        assert db.query(ProjectMove).count() == 1
        assert sum(isinstance(r, ProjectMove) for r in results) == 1


class TestRetryCancel:
    def test_retry_resets_the_failed_step(self, env):
        http, _, db = env
        move_id = _start(http).json()["id"]
        move = db.get(ProjectMove, move_id)
        failed = [dict(s) for s in move.steps]
        failed[0].update(status="failed", detail="boom")
        move.steps, move.status = failed, "failed"
        db.commit()

        response = http.post(f"/api/projects/alpha/moves/{move_id}/retry")

        body = response.json()
        assert body["status"] == "running"
        assert body["steps"][0]["status"] == "pending"

    def test_cancel_before_the_source_is_stopped(self, env):
        http, _, _ = env
        move_id = _start(http).json()["id"]

        with mock.patch.object(
            steps.project_registry, "set_move", new=mock.AsyncMock()
        ):
            response = http.post(f"/api/projects/alpha/moves/{move_id}/cancel")

        assert response.json()["status"] == "cancelled"


class TestLoop:
    def test_tick_advances_running_moves_only(self, env):
        http, _, db = env
        move_id = _start(http).json()["id"]
        db.add(
            ProjectMove(
                project="beta",
                source="aws",
                target="gcp",
                status="failed",
                steps=[],
                restores=[],
                actor="x",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        with (
            mock.patch.object(loop, "SessionLocal", return_value=db),
            mock.patch.object(db, "close"),
            mock.patch.object(
                steps, "advance", new=mock.AsyncMock()
            ) as advance,
        ):
            asyncio.run(loop.tick())

        assert [c.args[1].id for c in advance.call_args_list] == [move_id]

    def test_a_failing_move_does_not_stop_the_next_one(self, env):
        http, _, db = env
        first = _start(http).json()["id"]
        db.add(
            ProjectMove(
                project="beta",
                source="aws",
                target="gcp",
                status="running",
                steps=[],
                restores=[],
                actor="x",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        second = db.query(ProjectMove).filter_by(project="beta").one().id
        seen = []

        async def advance(_db, move):
            seen.append(move.id)
            if move.id == first:
                raise RuntimeError("commit failed")

        with (
            mock.patch.object(loop, "SessionLocal", return_value=db),
            mock.patch.object(db, "close"),
            mock.patch.object(steps, "advance", new=advance),
        ):
            asyncio.run(loop.tick())

        assert seen == [first, second]


class TestAudit:
    @pytest.mark.parametrize(
        "path,action",
        [
            ("/api/projects/alpha/move", "project.move"),
            ("/api/projects/alpha/moves/3/retry", "project.move.retry"),
            ("/api/projects/alpha/moves/3/cancel", "project.move.cancel"),
        ],
    )
    def test_routes_are_recorded(self, path, action):
        assert match_action("POST", path)[0] == action

    def test_a_move_is_notable(self):
        assert "project.move" in NOTABLE
