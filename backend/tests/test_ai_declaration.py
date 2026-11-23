"""The rules an agent declaration obeys, the catalogue and the stake policy."""

import ast
import pathlib
import re

import pytest

from app.services.agents import policy
from app.services.ai import catalog, declaration
from app.services.ai.declaration import DeclarationError, ProjectAi

CATALOGUE = {
    "web_search": ["untrusted_content", "external_send"],
    "run_python": ["executes_code"],
    "shop__get_order": ["private_data"],
    "shop__search_posts": ["untrusted_content"],
    "shop__reply_as_bot": ["writes", "external_send"],
}
APPS = [
    {"name": "shop", "type": "service"},
    {"name": "bridge", "type": "service"},
]


def _project(**overrides) -> ProjectAi:
    values = {
        "enabled": True,
        "models": ["claude-haiku-5-5", "claude-sonnet-5-5"],
        "web_search": True,
        "daily_token_budget": 1_000_000,
        "apps": [],
        "agents": [],
    }
    values.update(overrides)
    return ProjectAi(**values)


def _build(fields: dict, project: ProjectAi | None = None, name="helper"):
    base = {"instructions": "Answer.", "tools": []}
    base.update(fields)
    return declaration.build(
        "alpha",
        name,
        base,
        project or _project(),
        CATALOGUE,
        APPS,
        "alice",
        {name, "other"},
    )


def _errors(fields: dict, project: ProjectAi | None = None, name="helper"):
    with pytest.raises(DeclarationError) as caught:
        _build(fields, project, name)
    return " ".join(caught.value.errors)


def test_a_valid_agent_gets_its_rights_and_its_values_block():
    spec = _build({"tools": ["shop__search_posts"], "template": "assistant"})

    assert (spec.rights, spec.declaration) == (
        {
            "name": "helper",
            "owner": "alice",
            "models": ["claude-haiku-5-5"],
            "dailyTokenBudget": 100_000,
            "tools": ["shop__search_posts"],
            "isolation": "runc",
            "callers": [],
            "calls": [],
            "status": "active",
        },
        {
            "template": "assistant",
            "description": "",
            "model": "claude-haiku-5-5",
            "effort": "low",
            "maxSteps": 8,
            "trigger": {"type": "chat"},
            "instructions": "Answer.",
            "tools": ["shop__search_posts"],
        },
    )


def test_three_risks_together_are_refused_with_the_split_proposed():
    errors = _errors(
        {"tools": ["shop__get_order", "shop__search_posts", "web_search"]}
    )

    assert "Rule of Two" in errors and "app-bot" in errors


def test_running_code_raises_the_minimum_isolation_to_gvisor():
    spec = _build({"tools": ["run_python"]})

    assert spec.rights["isolation"] == "gvisor"


def test_an_isolation_weaker_than_the_tools_need_is_refused():
    assert "weaker" in _errors({"tools": ["run_python"], "isolation": "runc"})


def test_a_stronger_isolation_is_kept():
    spec = _build({"tools": ["run_python"], "isolation": "kata"})

    assert spec.rights["isolation"] == "kata"


@pytest.mark.parametrize(
    "fields, project, expected",
    [
        ({"tools": ["shop__drop_tables"]}, None, "not available"),
        ({"tools": ["web_search"]}, _project(web_search=False), "web search"),
        ({"model": "claude-opus-5-5"}, None, "not allowed"),
        ({"daily_token_budget": 2_000_000}, None, "above the project"),
        ({"trigger": {"type": "schedule"}}, None, "cron"),
        ({"trigger": {"type": "a2a"}}, None, "agent: caller"),
        ({"instructions": " "}, None, "required"),
        ({"max_steps": 99}, None, "max_steps"),
        ({"callers": ["app:ghost"]}, None, "no such app"),
        ({"calls": ["ghost"]}, None, "no such other agent"),
        ({}, _project(enabled=False), "not enabled"),
    ],
)
def test_each_broken_rule_is_named(fields, project, expected):
    assert expected in _errors(fields, project)


def test_a_name_that_would_overflow_the_namespace_is_refused():
    errors = declaration.check_name("x" * 40, "y" * 30)

    assert errors and "too long" in errors[0]


def test_bare_callers_are_agents():
    spec = _build(
        {"trigger": {"type": "a2a"}, "callers": ["other", "app:bridge"]}
    )

    assert spec.rights["callers"] == ["agent:other", "app:bridge"]


def test_a_schedule_keeps_its_cron():
    spec = _build({"trigger": {"type": "schedule", "cron": "0 8 * * *"}})

    assert spec.declaration["trigger"] == {
        "type": "schedule",
        "cron": "0 8 * * *",
    }


def test_the_capability_map_follows_the_badges():
    view = declaration.capabilities(
        ["shop__search_posts", "shop__reply_as_bot", "run_python"], CATALOGUE
    )

    assert view == {
        "reads": ["shop__search_posts"],
        "acts": ["shop__reply_as_bot"],
        "sends": ["shop__reply_as_bot"],
        "runs_code": ["run_python"],
    }


def test_fields_of_an_agent_rebuild_the_same_agent():
    spec = _build({"tools": ["run_python"], "description": "Calc"})

    again = _build(declaration.fields_of(spec.declaration, spec.rights))

    assert (again.rights, again.declaration) == (spec.rights, spec.declaration)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_app_mcp_tools_are_prefixed_and_badged():
    tools = catalog.mcp_tools(
        "shop",
        {
            "mcp": {
                "enabled": True,
                "tools": [
                    "get_thread",
                    "reply_as_bot",
                    {"name": "export", "badges": ["private_data", "bogus"]},
                ],
            }
        },
    )

    assert [(t["name"], t["badges"]) for t in tools] == [
        ("shop__get_thread", ["untrusted_content"]),
        ("shop__reply_as_bot", ["writes", "external_send"]),
        ("shop__export", ["private_data"]),
    ]


def test_tools_declared_with_their_final_names_are_not_prefixed_twice():
    tools = catalog.mcp_tools(
        "epitweet-mcp",
        {
            "mcp": {
                "enabled": True,
                "tools": [
                    "epitweet-mcp__get_thread",
                    "epitweet-mcp__reply_as_grok",
                ],
            }
        },
    )

    assert [(t["name"], t["badges"]) for t in tools] == [
        ("epitweet-mcp__get_thread", ["untrusted_content"]),
        ("epitweet-mcp__reply_as_grok", ["writes", "external_send"]),
    ]


def test_private_app_data_is_badged_private():
    tools = catalog.mcp_tools(
        "crm",
        {"mcp": {"enabled": True, "data": "private", "tools": ["get_x"]}},
    )

    assert tools[0]["badges"] == ["private_data"]


def test_an_app_without_mcp_has_no_tools():
    assert catalog.mcp_tools("shop", {"mcp": {"enabled": False}}) == []


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/3-Istor/epitweet", "3-Istor/epitweet"),
        ("https://github.com/3-istor/epitweet.git", "3-Istor/epitweet"),
        ("https://github.com/someone/epitweet", None),
        ("https://github.com/3-Istor/a/b", None),
        (None, None),
    ],
)
def test_only_repositories_of_the_org_are_written(url, expected):
    assert catalog.github_repo(url) == expected


def test_a_pair_template_reads_the_reader_from_the_top_and_the_actor_apart():
    item = catalog._template(  # pylint: disable=protected-access
        {
            "id": "app-bot",
            "recommended": {"dailyTokenBudget": 300000, "isolation": "gvisor"},
            "defaults": {"tools": ["web_search"]},
            "suggestedTools": [{"name": "web_search", "badges": []}],
            "pair": {
                "reader": {"suffix": "reader"},
                "actor": {
                    "suffix": "actor",
                    "recommended": {"dailyTokenBudget": 50000},
                    "defaults": {"maxSteps": 2},
                },
            },
        }
    )

    assert item["pair"] == {
        "reader": {
            "tools": ["web_search"],
            "isolation": "gvisor",
            "dailyTokenBudget": 300000,
        },
        "actor": {"maxSteps": 2, "dailyTokenBudget": 50000},
        "actor_suggested_tools": [],
    }


# ---------------------------------------------------------------------------
# Stake policy
# ---------------------------------------------------------------------------


GATED = re.compile(r"^(app|agent|ai)\.[a-z_]+$")


def _actions_used_in_code() -> set[str]:
    """Literal ``app.*``, ``agent.*`` and ``ai.*`` actions of the tools."""
    found = set()
    for path in pathlib.Path("app").glob("mcp_*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if GATED.match(node.value):
                    found.add(node.value)
    return found


def test_app_power_pause_has_a_stake_policy_rule():
    decision = policy.decide(
        "app.pause", policy.ProjectStake("p", "standard", None)
    )

    assert decision.outcome == "ask"


def test_every_action_the_tools_name_exists_in_the_policy():
    actions = policy.rules()["actions"]
    missing = sorted(a for a in _actions_used_in_code() if a not in actions)

    assert missing == []


@pytest.mark.parametrize(
    "action",
    ["ai.configure", "agent.create", "agent.update", "agent.delete"],
)
def test_ai_changes_never_run_on_a_critical_project(action):
    decision = policy.decide(
        action, policy.ProjectStake("p", "critical", None)
    )

    assert decision.outcome in ("deny", "confirm")


def test_the_kill_switch_is_never_slowed_down_below_critical():
    outcomes = {
        policy.decide(
            "agent.suspend", policy.ProjectStake("p", level, None)
        ).outcome
        for level in ("sandbox", "demo", "standard")
    }

    assert outcomes == {"allow"}
