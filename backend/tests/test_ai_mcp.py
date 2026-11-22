"""The AI tools of the CMP MCP server: the stake policy, then one
journaled CMP route per write."""

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest

from app import mcp_ai, mcp_operations, mcp_server
from app.services import audit
from app.services.agents import policy

WRITES = {
    "set_project_ai": (
        {"project": "demo", "enabled": True, "models": ["claude-haiku-5-5"]},
        "ai.configure",
    ),
    "enable_app_ai": ({"project": "demo", "app": "shop"}, "ai.configure"),
    "reconcile_project_ai": ({"project": "demo"}, "ai.configure"),
    "create_agent": (
        {
            "project": "demo",
            "template": "assistant",
            "name": "helper",
            "repo_app": "shop",
        },
        "agent.create",
    ),
    "update_agent": (
        {"project": "demo", "agent": "helper", "changes": {"tools": []}},
        "agent.update",
    ),
    "suspend_agent": ({"project": "demo", "agent": "helper"}, "agent.suspend"),
    "delete_agent": (
        {"project": "demo", "agent": "helper", "user_confirmed": True},
        "agent.delete",
    ),
}


class Api:
    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"agents": [], "pull_request": None}


@pytest.fixture
def cmp(monkeypatch):
    api = Api()
    caller = SimpleNamespace(
        api=api,
        require_write=mock.Mock(),
        require_role=mock.AsyncMock(),
        username="alice",
        grant_id=1,
        claims={"cnp_agent": {"client": "Claude Code"}},
    )
    for module in (mcp_server, mcp_operations, mcp_ai):
        monkeypatch.setattr(module, "Caller", lambda: caller)
    stake = {"level": "sandbox"}
    monkeypatch.setattr(
        policy,
        "project_stake",
        lambda project: policy.ProjectStake(project, stake["level"], None),
    )
    monkeypatch.setattr(policy, "request_confirmation", mock.Mock())
    monkeypatch.setattr(mcp_server.audit, "record_agent_event", mock.Mock())
    return SimpleNamespace(api=api, caller=caller, stake=stake)


def _run(name, arguments):
    tools = {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}
    return asyncio.run(tools[name].run(arguments))


@pytest.mark.parametrize("tool", sorted(WRITES))
def test_every_ai_write_goes_through_one_journaled_route(cmp, tool):
    arguments, _ = WRITES[tool]

    result = _run(tool, arguments)

    writes = [(m, p) for m, p, _ in cmp.api.calls if m != "GET"]
    assert result["ok"] is True
    assert len(writes) == 1 and audit.match_action(*writes[0])[0]


@pytest.mark.parametrize("tool", sorted(WRITES))
def test_ai_writes_wait_for_the_user_on_a_standard_project_or_never_run(
    cmp, tool
):
    arguments, action = WRITES[tool]
    cmp.stake["level"] = "critical"

    result = _run(tool, {**arguments, "user_confirmed": False})

    assert [c for c in cmp.api.calls if c[0] != "GET"] == []
    assert (result["ok"], action in policy.rules()["actions"]) == (False, True)


def test_an_assistant_gets_the_preview_and_nothing_is_written(cmp):
    result = _run(
        "create_agent",
        {**WRITES["create_agent"][0], "preview": True},
    )

    assert (result["preview"], cmp.api.calls) == (True, [])


def test_resuming_counts_as_an_update_not_as_a_kill_switch(cmp):
    cmp.stake["level"] = "standard"

    result = _run(
        "suspend_agent", {"project": "demo", "agent": "helper", "resume": True}
    )

    assert result["status"] == "needs_user_confirmation"


def test_suspending_needs_no_confirmation_on_a_standard_project(cmp):
    cmp.stake["level"] = "standard"

    result = _run("suspend_agent", {"project": "demo", "agent": "helper"})

    assert result["ok"] is True


def test_update_agent_refuses_fields_it_does_not_know(cmp):
    result = _run(
        "update_agent",
        {"project": "demo", "agent": "helper", "changes": {"status": "x"}},
    )

    assert (result["ok"], cmp.api.calls) == (False, [])
