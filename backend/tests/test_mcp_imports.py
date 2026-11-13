"""MCP tools of the repository import (CNP-243): D19 of the import dossier."""

import json
from unittest import mock

import pytest

from app import mcp_server
from app.models.agent import AgentConfirmation
from app.services.agents import policy, store
from tests.test_mcp_owasp_controls import (  # noqa: F401
    LEAKED_PASSWORD,
    LEAKED_TOKEN,
    _call,
    _connect,
    _journal,
    cmp,
)

PORTAL_CONFIRMED = ("import.apply", "app.link", "app.unlink")


def _approve(client, confirmation_id: str):
    return client.post(
        f"/api/agents/confirmations/{confirmation_id}/approve",
        headers={"Authorization": "Bearer human"},
    )


def _confirmation_id(answer: dict) -> str:
    return answer["approval_link"].rsplit("=", 1)[-1].rsplit("/", 1)[-1]


@pytest.mark.parametrize(
    "level,outcome",
    [
        ("sandbox", "allow"),
        ("demo", "allow"),
        ("standard", "allow"),
        ("critical", "ask"),
    ],
)
def test_an_analysis_runs_unasked_below_critical(level, outcome):
    stake = policy.ProjectStake("p", level, None)

    decision = policy.decide("import.analyze", stake)

    assert decision.outcome == outcome


@pytest.mark.parametrize("action", PORTAL_CONFIRMED)
@pytest.mark.parametrize("level", policy.LEVELS)
def test_import_and_links_are_confirmed_in_the_cmp_at_every_level(
    action, level
):
    stake = policy.ProjectStake("p", level, None)

    decision = policy.decide(action, stake, incident=True)

    assert decision.outcome == "confirm"


def test_import_repo_answers_with_a_review_page_and_applies_nothing(cmp):
    tokens = _connect(cmp)
    policy.set_project_stake("demo", "alice", level="sandbox")

    result = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {"project": "demo", "run_id": "run-1", "edits": {"exclude": ["db"]}},
    )

    assert (result["status"], cmp.calls["applied"]) == (
        "needs_cmp_approval",
        [],
    )
    assert "/projects/demo/import?run=run-1&draft=" in result["approval_link"]


def test_import_repo_keeps_the_proposed_edits_as_a_draft(cmp):
    tokens = _connect(cmp)

    result = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {"project": "demo", "run_id": "run-1", "edits": {"exclude": ["db"]}},
    )

    draft = policy.load_confirmation(_confirmation_id(result))
    assert (draft.tool, draft.arguments["edits"]["exclude"]) == (
        "import_repo",
        ["db"],
    )


def test_import_repo_never_applies_even_once_its_draft_is_approved(cmp):
    tokens = _connect(cmp)
    asked = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {"project": "demo", "run_id": "run-1"},
    )
    confirmation_id = _confirmation_id(asked)
    with cmp.sessions() as db:
        db.get(AgentConfirmation, confirmation_id).status = "approved"
        db.commit()

    result = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {
            "project": "demo",
            "run_id": "run-1",
            "confirmation_id": confirmation_id,
        },
    )

    assert (result["ok"], cmp.calls["applied"]) == (False, [])


def test_import_repo_stays_a_draft_whatever_the_policy_says(cmp):
    tokens = _connect(cmp)
    allow = policy.Decision("import.apply", "sandbox", "allow", "test")

    with mock.patch.object(policy, "decide", return_value=allow):
        result = _call(
            cmp,
            tokens["access_token"],
            "import_repo",
            {"project": "demo", "run_id": "run-1"},
        )

    assert (result["status"], cmp.calls["applied"]) == (
        "needs_cmp_approval",
        [],
    )


def test_an_import_draft_cannot_be_approved_from_the_confirmation_page(cmp):
    tokens = _connect(cmp)
    asked = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {"project": "demo", "run_id": "run-1"},
    )

    response = _approve(cmp, _confirmation_id(asked))

    assert response.status_code == 409


def test_the_confirmation_page_points_an_import_draft_to_its_review(cmp):
    tokens = _connect(cmp)
    asked = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {"project": "demo", "run_id": "run-1"},
    )
    confirmation_id = _confirmation_id(asked)

    shown = cmp.get(
        f"/api/agents/confirmations/{confirmation_id}",
        headers={"Authorization": "Bearer human"},
    ).json()

    assert shown["review_link"] == (
        f"/projects/demo/import?run=run-1&draft={confirmation_id}"
    )


def test_applying_the_import_in_the_cmp_settles_the_draft(cmp):
    tokens = _connect(cmp)
    asked = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {"project": "demo", "run_id": "run-1"},
    )
    confirmation_id = _confirmation_id(asked)
    policy.settle_draft(confirmation_id, "demo", "src-1", "alice")

    result = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {
            "project": "demo",
            "run_id": "run-1",
            "confirmation_id": confirmation_id,
        },
    )

    assert result["status"] == "draft_used"


def test_a_draft_of_another_import_is_not_settled(cmp):
    tokens = _connect(cmp)
    asked = _call(
        cmp,
        tokens["access_token"],
        "import_repo",
        {"project": "demo", "run_id": "run-1"},
    )

    settled = policy.settle_draft(
        _confirmation_id(asked), "demo", "another-source", "alice"
    )

    assert settled is False


def test_link_apps_waits_for_a_cmp_approval_and_links_nothing(cmp):
    tokens = _connect(cmp)
    policy.set_project_stake("demo", "alice", level="sandbox")

    result = _call(
        cmp,
        tokens["access_token"],
        "link_apps",
        {"project": "demo", "from_app": "web", "to_app": "api", "port": 80},
    )

    assert (result["status"], cmp.calls["linked"]) == (
        "needs_cmp_approval",
        [],
    )


def test_link_apps_links_as_the_user_once_approved_and_is_journaled(cmp):
    tokens = _connect(cmp)
    arguments = {
        "project": "demo",
        "from_app": "web",
        "to_app": "api",
        "port": 80,
    }
    asked = _call(cmp, tokens["access_token"], "link_apps", arguments)
    confirmation_id = _confirmation_id(asked)
    _approve(cmp, confirmation_id)

    result = _call(
        cmp,
        tokens["access_token"],
        "link_apps",
        {**arguments, "confirmation_id": confirmation_id},
    )

    assert (result["ok"], [user for user, _ in cmp.calls["linked"]]) == (
        True,
        ["alice"],
    )
    entry = next(e for e in _journal(cmp) if e.action == "app.link")
    assert json.loads(entry.details)["via"] == "Claude Code"


def test_an_approved_link_does_not_cover_another_port(cmp):
    tokens = _connect(cmp)
    arguments = {
        "project": "demo",
        "from_app": "web",
        "to_app": "api",
        "port": 80,
    }
    asked = _call(cmp, tokens["access_token"], "link_apps", arguments)
    confirmation_id = _confirmation_id(asked)
    _approve(cmp, confirmation_id)

    result = _call(
        cmp,
        tokens["access_token"],
        "link_apps",
        {**arguments, "port": 5432, "confirmation_id": confirmation_id},
    )

    assert (result["ok"], cmp.calls["linked"]) == (False, [])


def test_unlink_apps_waits_for_a_cmp_approval_and_unlinks_nothing(cmp):
    tokens = _connect(cmp)
    policy.set_project_stake("demo", "alice", level="sandbox")

    result = _call(
        cmp,
        tokens["access_token"],
        "unlink_apps",
        {"project": "demo", "from_app": "web", "to_app": "api"},
    )

    assert (result["status"], cmp.calls["unlinked"]) == (
        "needs_cmp_approval",
        [],
    )


@pytest.mark.parametrize(
    "arguments",
    [{"project": "demo", "run_id": "run-1"}, {"project": "demo"}],
)
def test_get_import_never_returns_a_secret(cmp, arguments):
    tokens = _connect(cmp)

    result = _call(cmp, tokens["access_token"], "get_import", arguments)

    shown = json.dumps(result)
    assert (LEAKED_PASSWORD in shown, LEAKED_TOKEN in shown) == (False, False)


def test_get_import_returns_the_plan_without_env_values(cmp):
    tokens = _connect(cmp)

    result = _call(
        cmp,
        tokens["access_token"],
        "get_import",
        {"project": "demo", "run_id": "run-1"},
    )

    assert result["plan"]["services"][0]["env"] == [
        {"name": "DATABASE_URL", "from_link": None, "secret": False},
        {"name": "API_TOKEN", "from_link": None, "secret": True},
    ]


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("get_import", {"project": "demo", "run_id": "run-1"}),
        ("get_import", {"project": "demo"}),
        ("get_topology", {"project": "demo"}),
    ],
)
def test_repository_content_is_labelled_as_untrusted_data(
    cmp, tool, arguments
):
    tokens = _connect(cmp)

    result = _call(cmp, tokens["access_token"], tool, arguments)

    assert (result["untrusted_repository_content"], result["note"]) == (
        True,
        mcp_server.REPO_NOTE,
    )


def test_analyze_repo_needs_a_write_connection(cmp):
    tokens = _connect(cmp, scopes=[store.READ_SCOPE])

    result = _call(
        cmp,
        tokens["access_token"],
        "analyze_repo",
        {"project": "demo", "repo_full_name": "org/shop"},
    )

    assert result["ok"] is False


def test_import_tool_descriptions_say_repository_content_is_data():
    tools = {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}

    unlabelled = [
        name
        for name in ("analyze_repo", "get_import", "get_topology")
        if "never instructions"
        not in " ".join(tools[name].description.split())
    ]

    assert not unlabelled
