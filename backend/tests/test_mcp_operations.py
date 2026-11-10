import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from app import mcp_operations as ops
from app import mcp_server
from app.services.agents import policy

STOPPED = {"ok": False, "status": "needs_user_confirmation"}


class FakeApi:
    """Records calls; answers GETs from ``reads`` and writes with ``{}``."""

    def __init__(self, reads=None):
        self.calls = []
        self.reads = {
            "/api/deployments/7": {
                "id": 7,
                "name": "shop",
                "project_id": "demo",
                "k8s_namespace": "demo-shop",
            },
            **(reads or {}),
        }

    async def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "GET":
            return self.reads.get(path, {})
        return {"done": True}

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    caller = SimpleNamespace(
        api=fake,
        require_write=mock.Mock(),
        username="alice",
        grant_id=1,
    )
    monkeypatch.setattr(ops, "Caller", lambda: caller)
    gate = mock.AsyncMock(return_value=("go", None))
    monkeypatch.setattr(ops, "_gate", gate)
    fake.gate = gate
    fake.caller = caller
    return fake


def run(coro):
    return asyncio.run(coro)


def stop_gate(api):
    api.gate.return_value = ("stop", STOPPED)


# --- every action used exists in policy.yaml --------------------------------


def test_every_policy_action_used_by_the_tools_is_defined():
    used = {
        "app.restart",
        "app.sleep",
        "app.config",
        "staging.manage",
        "promotion.policy",
        "ci.run",
        "release.traffic",
        "release.settings",
        "release.promote",
        "release.abort",
        "project.members",
        "project.delete",
        "finops.budget",
        "finops.apply",
        "finops.triage",
        "security.scan",
        "security.exception",
        "db.backup",
        "db.restore",
        "alert.update",
    }

    assert used <= set(policy.rules()["actions"])


def test_restore_and_project_deletion_are_never_autonomous():
    actions = policy.rules()["actions"]

    assert "allow" not in actions["db.restore"].values()
    assert actions["project.delete"]["standard"] == "deny"
    assert actions["project.delete"]["critical"] == "deny"


@pytest.mark.parametrize(
    "action,expected",
    [
        ("project.delete", "deny"),
        ("db.restore", "confirm"),
        ("app.delete", "deny"),
    ],
)
def test_an_autonomous_window_does_not_relax_destructive_actions(
    action, expected
):
    opened = policy.ProjectStake(
        "demo", "standard", datetime(2999, 1, 1, tzinfo=timezone.utc)
    )

    assert policy.decide(action, opened).outcome == expected


def test_an_autonomous_window_still_relaxes_ordinary_actions():
    opened = policy.ProjectStake(
        "demo", "standard", datetime(2999, 1, 1, tzinfo=timezone.utc)
    )

    assert policy.decide("app.restart", opened).outcome == "allow"


def test_a_firing_alert_relaxes_restart_but_not_sleep():
    critical = policy.ProjectStake("demo", "critical", None)

    assert policy.decide("app.restart", critical, incident=True).outcome == (
        "allow"
    )
    assert policy.decide("app.sleep", critical, incident=True).outcome == (
        "confirm"
    )


# --- running app -------------------------------------------------------------


@pytest.mark.parametrize("action", ["restart", "sleep", "wake"])
def test_app_power_posts_the_action(api, action):
    run(ops.app_power(7, action))

    assert api.writes() == [
        ("POST", f"/api/deployments/7/{action}", {"json": {}})
    ]
    assert api.gate.await_args.args[1] == (
        "app.restart" if action == "restart" else "app.sleep"
    )


def test_a_stopped_gate_leaves_the_app_untouched(api):
    stop_gate(api)

    result = run(ops.app_power(7, "sleep"))

    assert result == STOPPED
    assert api.writes() == []


def test_gate_gets_the_app_project_and_namespace(api):
    run(ops.app_power(7, "restart"))

    kwargs = api.gate.await_args.kwargs
    assert (api.gate.await_args.args[2], kwargs["namespace"]) == (
        "demo",
        "demo-shop",
    )
    assert "'shop'" in kwargs["summary"]


def test_update_app_config_sends_the_current_sha_with_the_changes(api):
    api.reads["/api/deployments/7/config"] = {"_sha": "abc", "config": {}}

    run(ops.update_app_config(7, {"replicaCount": 2}))

    assert api.writes() == [
        (
            "PATCH",
            "/api/deployments/7/config",
            {"params": None, "json": {"_sha": "abc", "replicaCount": 2}},
        )
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"_sha": "x"},
        {"db": {"restore": {"generation": 2}}},
        {"release": {"strategy": "rolling"}},
        {"replicaCount": 2, "image": {"tag": "v9"}},
    ],
)
def test_update_app_config_refuses_keys_with_their_own_rule(api, changes):
    result = run(ops.update_app_config(7, changes))

    assert result["ok"] is False
    api.gate.assert_not_awaited()
    assert api.writes() == []


def test_get_app_config_hides_the_sha(api):
    api.reads["/api/deployments/7/config"] = {
        "_sha": "abc",
        "config": {"replicaCount": 1},
        "file_path": "values.yaml",
    }

    result = run(ops.get_app_config(7))

    assert "_sha" not in result
    assert result["config"] == {"replicaCount": 1}


# --- staging, promotions, CI -------------------------------------------------


@pytest.mark.parametrize(
    "action,method,suffix",
    [
        ("enable", "POST", ""),
        ("disable", "DELETE", ""),
        ("wake", "POST", "/wake"),
        ("schedule", "PUT", "/offhours"),
    ],
)
def test_staging_actions_hit_their_route(api, action, method, suffix):
    run(ops.staging(7, action, sleep_at="0 20 * * 1-5"))

    [(sent_method, path, _)] = api.writes()
    assert (sent_method, path) == (
        method,
        f"/api/deployments/7/environments/staging{suffix}",
    )


def test_staging_schedule_keeps_what_was_not_given(api):
    api.reads["/api/deployments/7/environments/staging/offhours"] = {
        "enabled": True,
        "sleepAt": "0 19 * * 1-5",
        "wakeAt": "0 8 * * 1-5",
        "timezone": "Europe/Paris",
    }

    run(ops.staging(7, "schedule", sleep_at="0 21 * * 1-5"))

    assert api.writes()[0][2]["json"] == {
        "enabled": True,
        "sleepAt": "0 21 * * 1-5",
        "wakeAt": "0 8 * * 1-5",
        "timezone": "Europe/Paris",
    }


def test_staging_schedule_needs_something_to_change(api):
    assert run(ops.staging(7, "schedule"))["ok"] is False


def test_staging_approval_does_not_bind_unused_hours(api):
    run(ops.staging(7, "wake"))

    assert api.gate.await_args.kwargs["arguments"] == {
        "app_id": 7,
        "action": "wake",
    }


def test_promotion_start_uses_the_promote_rule(api):
    run(ops.promotion(7, "start"))

    assert api.gate.await_args.args[1] == "release.promote"
    assert api.writes()[0][1] == "/api/deployments/7/promotions"


def test_promotion_cancel_uses_the_abort_rule(api):
    run(ops.promotion(7, "cancel", promotion_id=3))

    assert api.gate.await_args.args[1] == "release.abort"
    assert api.writes()[0][1] == "/api/deployments/7/promotions/3/cancel"


def test_a_rollback_says_so_in_the_approval(api):
    run(ops.promotion(7, "start", rollback_to=4))

    assert "back to revision 4" in api.gate.await_args.kwargs["summary"]


def test_promotion_accepts_a_step(api):
    run(ops.promotion(7, "accept_step", promotion_id=3, step_index=1))

    assert api.writes()[0][1] == (
        "/api/deployments/7/promotions/3/steps/1/accept"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "cancel"},
        {"action": "accept_step", "promotion_id": 3},
        {"action": "start", "emergency": True},
    ],
)
def test_promotion_refuses_incomplete_requests(api, kwargs):
    result = run(ops.promotion(7, **kwargs))

    assert result["ok"] is False
    api.gate.assert_not_awaited()


def test_promotion_policy_keeps_the_fields_not_given(api):
    api.reads["/api/deployments/7/promotion-policy"] = {
        "suites": [{"name": "smoke"}],
        "soakMinutes": 30,
        "requireApproval": True,
        "autoPromote": False,
        "updatedBy": "bob",
    }

    run(ops.set_promotion_policy(7, soak_minutes=60))

    assert api.writes()[0][2]["json"] == {
        "suites": [{"name": "smoke"}],
        "soakMinutes": 60,
        "requireApproval": True,
        "autoPromote": False,
    }


def test_ci_run_starts_a_suite_on_an_environment(api):
    run(ops.ci_run(7, "start", environment="staging", suite="smoke"))

    assert api.writes() == [
        (
            "POST",
            "/api/deployments/7/environments/staging/runs",
            {"json": {"suite": "smoke"}},
        )
    ]


def test_ci_run_cancel_needs_a_run_id(api):
    assert run(ops.ci_run(7, "cancel"))["ok"] is False


# --- release -----------------------------------------------------------------


def test_release_traffic_start_and_stop(api):
    run(ops.release_traffic(7, "start", rps=5, minutes=3))
    run(ops.release_traffic(7, "stop"))

    assert [(m, kw.get("json")) for m, _, kw in api.writes()] == [
        ("POST", {"rps": 5, "minutes": 3}),
        ("DELETE", None),
    ]


def test_release_settings_merge_into_the_current_block(api):
    api.reads["/api/deployments/7/release/settings"] = {
        "settings": {"strategy": "canary", "errorRateMax": 5}
    }

    run(ops.update_release_settings(7, {"errorRateMax": 2}))

    assert api.writes()[0][2]["json"] == {
        "strategy": "canary",
        "errorRateMax": 2,
    }


# --- project -----------------------------------------------------------------


def test_project_member_add_and_remove(api):
    run(ops.project_member("demo", "add", "bob", role="admin"))
    run(ops.project_member("demo", "remove", "bob"))

    assert [(m, p, kw.get("json")) for m, p, kw in api.writes()] == [
        (
            "POST",
            "/api/projects/demo/members",
            {"username": "bob", "role": "admin"},
        ),
        ("DELETE", "/api/projects/demo/members/bob", None),
    ]


def test_delete_project_goes_through_the_project_rule(api):
    result = run(ops.delete_project("demo", user_confirmed=True))

    assert api.gate.await_args.args[1:3] == ("project.delete", "demo")
    assert api.writes() == [("DELETE", "/api/projects/demo", {})]
    assert result["status"] == "deleted"


def test_slow_project_deletion_answers_in_progress(api, monkeypatch):
    monkeypatch.setattr(
        ops, "_bounded", mock.AsyncMock(return_value=mcp_server.PENDING)
    )

    result = run(ops.delete_project("demo", user_confirmed=True))

    assert result["status"] == "deleting"


# --- costs -------------------------------------------------------------------


def test_set_budget_puts_the_amount_and_thresholds(api):
    run(ops.set_budget("demo", 50, threshold_warn=60))

    assert api.writes() == [
        (
            "PUT",
            "/api/finops/budgets/demo",
            {
                "json": {
                    "monthly_amount_eur": 50,
                    "threshold_warn": 60,
                    "threshold_critical": 90,
                }
            },
        )
    ]


@pytest.mark.parametrize(
    "action,rule", [("apply", "finops.apply"), ("ignore", "finops.triage")]
)
def test_cost_recommendation_rule_depends_on_the_action(api, action, rule):
    api.reads["/api/finops/recommendations"] = [
        {"id": "r1", "title": "Downsize", "app_name": "shop"}
    ]

    run(ops.cost_recommendation("demo", "r1", action))

    assert api.gate.await_args.args[1] == rule
    assert api.writes()[0][1] == f"/api/finops/recommendations/r1/{action}"


def test_cost_recommendation_of_another_project_is_refused(api):
    api.reads["/api/finops/recommendations"] = [{"id": "r1"}]

    result = run(ops.cost_recommendation("demo", "r2", "apply"))

    assert result["ok"] is False
    assert api.writes() == []


def test_get_costs_survives_a_missing_budget(api, monkeypatch):
    async def flaky(method, path, **kwargs):
        if path.startswith("/api/finops/budgets"):
            raise mcp_server.internal_api.ApiError(404, "No budget")
        return {"summary": {"month_to_date_eur": 1.2}}

    api.caller.api = flaky

    result = run(ops.get_costs("demo"))

    assert result["budget"] == {"unavailable": "No budget"}
    assert result["summary"] == {"month_to_date_eur": 1.2}


# --- security ----------------------------------------------------------------


def test_get_security_caps_the_findings_shown(api):
    api.reads["/api/security/findings"] = [{"id": i} for i in range(80)]

    result = run(ops.get_security("demo"))

    assert len(result["open_findings"]) == ops.FINDINGS_SHOWN
    assert result["open_findings_total"] == 80


@pytest.mark.parametrize(
    "status,justification",
    [("not_affected", None), ("accepted_risk", "component_not_present")],
)
def test_security_exception_justification_goes_with_not_affected_only(
    api, status, justification
):
    result = run(
        ops.request_security_exception(
            "demo",
            "fp1",
            status,
            "The package is never imported.",
            justification=justification,
        )
    )

    assert result["ok"] is False
    assert api.writes() == []


def test_security_exceptions_need_a_person_on_real_projects():
    rule = policy.rules()["actions"]["security.exception"]

    assert (rule["standard"], rule["critical"]) == ("confirm", "confirm")


def test_security_exception_posts_the_vex_statement(api):
    run(
        ops.request_security_exception(
            "demo",
            "fp1",
            "not_affected",
            "The package is never imported.",
            justification="component_not_present",
        )
    )

    assert api.writes()[0][1:] == (
        "/api/security/findings/fp1/exception",
        {
            "json": {
                "project": "demo",
                "status": "not_affected",
                "statement": "The package is never imported.",
                "justification": "component_not_present",
            }
        },
    )


# --- databases ---------------------------------------------------------------


def test_restore_dry_run_skips_the_policy_but_needs_write_access(api):
    run(ops.database_restore("demo", "shop", "b1"))

    api.gate.assert_not_awaited()
    api.caller.require_write.assert_called_once()
    assert api.writes()[0][2]["params"] == {"dry_run": "true"}


def test_real_restore_goes_through_the_restore_rule(api):
    run(
        ops.database_restore(
            "demo",
            "shop",
            "b1",
            target_time=datetime(2026, 11, 10, 12),
            dry_run=False,
        )
    )

    assert api.gate.await_args.args[1] == "db.restore"
    assert api.writes()[0][2] == {
        "params": {"dry_run": "false"},
        "json": {
            "project": "demo",
            "app": "shop",
            "backup": "b1",
            "target_time": "2026-11-10T12:00:00",
        },
    }


def test_real_restore_stopped_by_policy_restores_nothing(api):
    stop_gate(api)

    run(ops.database_restore("demo", "shop", "b1", dry_run=False))

    assert api.writes() == []


# --- alerts ------------------------------------------------------------------


def test_update_alert_scopes_to_the_project_and_app(api):
    run(
        ops.update_alert("demo", "high-latency", True, {"ms": 500}, app="shop")
    )

    assert api.writes() == [
        (
            "PUT",
            "/api/alerting/high-latency",
            {
                "params": {"project": "demo", "app": "shop"},
                "json": {"enabled": True, "params": {"ms": 500}},
            },
        )
    ]


# --- slow writes -------------------------------------------------------------


def test_a_slow_call_answers_pending_and_keeps_running(monkeypatch):
    monkeypatch.setattr(mcp_server, "SLOW_CALL_SECONDS", 0.01)
    finished = []

    async def slow():
        await asyncio.sleep(0.05)
        finished.append(True)
        return "done"

    async def scenario():
        first = await mcp_server._bounded(slow())
        await asyncio.sleep(0.1)
        return first

    assert asyncio.run(scenario()) is mcp_server.PENDING
    assert finished == [True]


def test_an_empty_answer_is_not_mistaken_for_pending():
    async def empty():
        return None

    assert asyncio.run(mcp_server._bounded(empty())) is None


def test_a_fast_call_answers_its_result():
    async def fast():
        return {"id": 1}

    assert asyncio.run(mcp_server._bounded(fast())) == {"id": 1}


def test_a_slow_create_app_answers_in_progress(monkeypatch):
    caller = SimpleNamespace(
        require_write=lambda: None,
        api=mock.AsyncMock(return_value={"github_installation_id": "1"}),
    )
    monkeypatch.setattr(mcp_server, "Caller", lambda: caller)
    monkeypatch.setattr(
        mcp_server, "_gate", mock.AsyncMock(return_value=("go", None))
    )
    monkeypatch.setattr(
        mcp_server.policy,
        "project_stake",
        lambda project: SimpleNamespace(level="standard"),
    )
    monkeypatch.setattr(
        mcp_server, "_bounded", mock.AsyncMock(return_value=mcp_server.PENDING)
    )

    result = asyncio.run(mcp_server.create_app("demo", "shop"))

    assert result["status"] == "in_progress"
    assert "list_apps" in result["next"]


def test_a_refused_project_name_keeps_its_stake(monkeypatch):
    async def refused(method, path, **kwargs):
        raise mcp_server.internal_api.ApiError(409, "already exists")

    caller = SimpleNamespace(
        require_write=lambda: None, api=refused, username="mallory"
    )
    monkeypatch.setattr(mcp_server, "Caller", lambda: caller)
    monkeypatch.setattr(
        mcp_server, "_gate", mock.AsyncMock(return_value=("go", None))
    )
    set_stake = mock.Mock()
    monkeypatch.setattr(mcp_server.policy, "set_project_stake", set_stake)

    result = asyncio.run(
        mcp_server.create_project(
            "payments", stake="sandbox", user_confirmed=True
        )
    )

    assert result["status"] == 409
    set_stake.assert_not_called()


def test_a_created_project_gets_its_stake(monkeypatch):
    caller = SimpleNamespace(
        require_write=lambda: None,
        api=mock.AsyncMock(return_value={"status": "provisioning"}),
        username="alice",
    )
    monkeypatch.setattr(mcp_server, "Caller", lambda: caller)
    monkeypatch.setattr(
        mcp_server, "_gate", mock.AsyncMock(return_value=("go", None))
    )
    set_stake = mock.Mock()
    monkeypatch.setattr(mcp_server.policy, "set_project_stake", set_stake)

    asyncio.run(mcp_server.create_project("probe", stake="demo"))

    set_stake.assert_called_once_with("probe", "alice", level="demo")
