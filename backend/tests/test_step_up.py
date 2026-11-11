import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Annotated
from unittest import mock

import pytest
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.security import SecurityException
from app.routers import projects, security
from app.services import step_up
from app.services.keycloak_service import get_current_user
from app.services.security.model import Tier
from tests.test_platform_only_endpoints import session_factory  # noqa: F401

NOW = 1_800_000_000


def _token(**overrides) -> dict:
    claims = {
        "sub": "u1",
        "preferred_username": "alice",
        "acr": "gold",
        "auth_time": NOW - 10,
    }
    claims.update(overrides)
    return claims


@pytest.fixture(autouse=True)
def step_up_on():
    with (
        mock.patch.object(settings, "STEP_UP_ENABLED", True),
        mock.patch.object(settings, "STEP_UP_ACR", "gold"),
        mock.patch.object(settings, "STEP_UP_MAX_AGE", 300),
        mock.patch.object(step_up.time, "time", return_value=NOW),
    ):
        yield


class TestIsSteppedUp:
    def test_a_recent_passkey_login_is_stepped_up(self):
        assert step_up.is_stepped_up(_token(), 300) is True

    @pytest.mark.parametrize(
        "claims",
        [
            pytest.param({"acr": "silver"}, id="password-and-otp level"),
            pytest.param({"acr": None}, id="no acr"),
            pytest.param({"auth_time": None}, id="no auth_time"),
            pytest.param(
                {"auth_time": "1799999990"}, id="auth_time not a number"
            ),
            pytest.param({"auth_time": True}, id="auth_time a boolean"),
            pytest.param(
                {"auth_time": NOW - 301}, id="login older than max_age"
            ),
            pytest.param({"auth_time": NOW + 60}, id="login in the future"),
        ],
    )
    def test_a_token_is_not_stepped_up_when(self, claims):
        assert step_up.is_stepped_up(_token(**claims), 300) is False


class TestAssertStepUp:
    def test_returns_a_401_challenge_with_acr_values_and_max_age(self):
        with pytest.raises(HTTPException) as exc:
            step_up.assert_step_up(_token(acr="silver"), 120)

        assert exc.value.status_code == 401
        assert exc.value.detail["code"] == "step_up_required"
        assert exc.value.detail["acr_values"] == "gold"
        assert exc.value.detail["max_age"] == 120
        header = exc.value.headers["WWW-Authenticate"]
        assert 'error="insufficient_user_authentication"' in header
        assert 'acr_values="gold"' in header
        assert "max_age=120" in header

    def test_uses_the_configured_max_age_by_default(self):
        with pytest.raises(HTTPException) as exc:
            step_up.assert_step_up(_token(auth_time=NOW - 301))

        assert exc.value.detail["max_age"] == 300

    def test_lets_everything_through_when_disabled(self):
        with mock.patch.object(settings, "STEP_UP_ENABLED", False):
            step_up.assert_step_up(_token(acr="silver"))

    def test_refuses_an_assistant_token_with_403_by_default(self):
        agent = _token(cnp_agent={"grant_id": 1})

        with pytest.raises(HTTPException) as exc:
            step_up.assert_step_up(agent)

        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "step_up_unavailable_for_agents"

    def test_lets_an_assistant_through_where_its_policy_gates_the_action(self):
        agent = _token(acr=None, cnp_agent={"grant_id": 1})

        step_up.assert_step_up(agent, agents_allowed=True)


class TestRequireStepUpDependency:
    @pytest.fixture
    def client(self):
        app = FastAPI()
        current = {"claims": _token()}
        app.dependency_overrides[get_current_user] = lambda: current["claims"]

        @app.post("/danger")
        def danger(
            token: Annotated[dict, Depends(step_up.require_step_up(60))],
        ):
            return {"user": token["preferred_username"]}

        return TestClient(app), current

    def test_runs_the_route_for_a_stepped_up_token(self, client):
        http, _ = client

        response = http.post("/danger")

        assert response.json() == {"user": "alice"}

    def test_answers_401_with_the_hint_before_the_route_runs(self, client):
        http, current = client
        current["claims"] = _token(auth_time=NOW - 61)

        response = http.post("/danger")

        assert response.status_code == 401
        assert response.json()["detail"]["max_age"] == 60
        assert "acr_values" in response.headers["www-authenticate"]


AUDITOR = {
    "sub": "a",
    "preferred_username": "aud",
    "groups": ["security-auditor"],
}
TODAY = date(2026, 11, 11)


class TestExceptionApproval:
    def _approve(self, factory, token, tier):
        with factory() as db:
            db.add(
                SecurityException(
                    project="shop",
                    app="web",
                    fingerprint="f1",
                    rule="CVE-1",
                    kind="vuln",
                    status="not_affected",
                    statement="s",
                    expires_on=TODAY + timedelta(days=30),
                    author="dev",
                )
            )
            db.commit()
            row_id = db.query(SecurityException).one().id
        with (
            factory() as db,
            mock.patch.object(
                security, "_exception_tier", lambda db, row: tier
            ),
            mock.patch.object(
                security,
                "_sync_exceptions_file",
                mock.AsyncMock(return_value=None),
            ),
        ):
            asyncio.run(security.approve_exception(row_id, token, db))
            return db.get(SecurityException, row_id)

    def test_approving_a_critical_exception_requires_a_step_up(
        self, session_factory
    ):
        with pytest.raises(HTTPException) as exc:
            self._approve(
                session_factory, {**AUDITOR, "acr": "silver"}, Tier.CORE
            )

        assert exc.value.status_code == 401

    def test_a_stepped_up_auditor_approves_a_critical_exception(
        self, session_factory
    ):
        token = {**AUDITOR, "acr": "gold", "auth_time": NOW - 5}

        row = self._approve(session_factory, token, Tier.CORE)

        assert row.approved_by_reviewer is True

    def test_approving_a_non_critical_exception_needs_no_step_up(
        self, session_factory
    ):
        row = self._approve(
            session_factory, {**AUDITOR, "acr": "silver"}, Tier.IMPORTANT
        )

        assert row.approved_at is not None


class TestDeleteProject:
    def test_a_project_is_not_torn_down_without_a_step_up(self):
        ctx = SimpleNamespace(project_name="gamma", token=_token(acr="silver"))
        db = mock.Mock()

        with (
            mock.patch.object(
                projects, "remove_record", new=mock.AsyncMock()
            ) as remove,
            pytest.raises(HTTPException) as exc,
        ):
            asyncio.run(projects.delete_project(ctx, BackgroundTasks(), db))

        assert exc.value.status_code == 401
        remove.assert_not_called()
        db.query.assert_not_called()
