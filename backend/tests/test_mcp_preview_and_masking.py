"""
CNP-311: every MCP write tool previews its change without applying it, and
no MCP answer carries a secret value back to the model.

Every registered tool runs here against a CMP whose answers are laced with
known secrets, through the real stake policy gate on a sandbox project.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi.encoders import jsonable_encoder

from app import mcp_ai
from app import mcp_operations as ops
from app import mcp_server
from app.services.agents import internal_api, policy
from app.services.agents import repo as repo_ops
from app.services.agents import skills, store
from app.services.agents.redact import MASK, mask

VAULT_TOKEN = "hvs." + "CAESIJ2kb8Xq" * 8
GITHUB_TOKEN = "ghp_" + "Zq3" * 12
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJlLXZhbHVl"  # gitleaks:allow
DB_PASSWORD = "s3cr3t-Db-Pa55-w0rd"  # gitleaks:allow
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYzEXAMPLEKEY"
STRIPE_KEY = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
PRIVATE_KEY_BODY = "MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6"  # gitleaks:allow
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    f"{PRIVATE_KEY_BODY}\n"
    "-----END RSA PRIVATE KEY-----"
)
KNOWN_SECRETS = (
    VAULT_TOKEN,
    GITHUB_TOKEN,
    JWT,
    DB_PASSWORD,
    AWS_SECRET,
    STRIPE_KEY,
    PRIVATE_KEY_BODY,
)
LOG_LINE = f"connecting with password={DB_PASSWORD} token {GITHUB_TOKEN}"
LEAK = {
    "password": DB_PASSWORD,
    "vault": {"token": VAULT_TOKEN, "data": {"API_KEY": STRIPE_KEY}},
    "env": [
        {"name": "DB_PASSWORD", "value": DB_PASSWORD},
        {"name": "GITHUB", "value": GITHUB_TOKEN},
    ],
    "DATABASE_URL": f"postgresql://app:{DB_PASSWORD}@db:5432/app",
    "aws_secret_access_key": AWS_SECRET,
    "tls": PRIVATE_KEY,
    "message": f"auth failed: Authorization: Bearer {JWT}",
    "line": LOG_LINE,
}
APP = {
    "id": 7,
    "name": "shop",
    "project_id": "demo",
    "status": "running",
    "k8s_namespace": "demo-shop",
    "github_repo_url": "https://github.com/org/shop",
    "template_id": "k3s-gitops-app",
    "step_message": LOG_LINE,
    "terraform_outputs": json.dumps(
        {"app_url": f"https://admin:{DB_PASSWORD}@shop.example.com"}
    ),
    "app_config": "{}",
}
RUN = {
    "id": "run-1",
    "source_id": "src-1",
    "kind": "analyze",
    "status": "succeeded",
    "requested_by": "alice",
    "report": {"error": LOG_LINE},
    "failure": None,
}
PLAN = {
    "services": [
        {
            "name": "api",
            "env": [{"name": "DB_PASSWORD", "value": DB_PASSWORD}],
            "build": {"notes": LOG_LINE},
        }
    ],
    "warnings": [LEAK["message"]],
}
RELEASE = {
    "strategy": "canary",
    "phase": "Degraded",
    "stableImage": "ghcr.io/org/shop:v1.0.0",
    "steps": [{"label": "analysis", "status": "failed", "message": LOG_LINE}],
}


def _get(path: str):  # pylint: disable=too-many-return-statements
    obs = re.match(r"/api/deployments/7/observability(/.*)$", path)
    if obs:
        return {
            "/signals": {
                "no_traffic": False,
                "range": "1h",
                "rate": [{"value": 1.0}],
                "error_percent": [{"value": 0.0}],
                "p50_ms": [{"value": 12.0}],
                "p95_ms": [{"value": 80.0}],
                "routes": [{"route": f"/login?token={GITHUB_TOKEN}"}],
            },
            "/alerts": {"alerts": [LEAK], "silences": []},
            "/slo": {
                "settings": {"period_days": 30},
                "objectives": [{"name": "availability", "note": LOG_LINE}],
            },
        }.get(obs.group(1), [LEAK])
    exact = {
        "/api/projects/": [{"name": "demo", "role": "owner", **LEAK}],
        "/api/projects/demo/apps": [APP],
        "/api/deployments/7": APP,
        "/api/deployments/7/release": RELEASE,
        "/api/deployments/7/release/choices": {
            "commitSha": "abc1234def",
            "staged": False,
            "choices": [{"kind": "patch", "tag": "v1.0.1"}],
        },
        "/api/deployments/7/releases": [
            {
                "revision": 3,
                "outcome": "failed",
                "started_at": "2026-11-18T10:00:00Z",
                "image": "ghcr.io/org/shop:v1.0.1",
            }
        ],
        "/api/deployments/7/config": {
            "_sha": "abc",
            "file_path": "deploy/values.yaml",
            "config": {
                "replicaCount": 1,
                "env": LEAK["env"],
                "externalDatabase": {"url": LEAK["DATABASE_URL"]},
            },
        },
        "/api/deployments/7/promotion-policy": {
            "suites": [],
            "soakMinutes": 10,
            "requireApproval": False,
            "autoPromote": False,
        },
        "/api/deployments/7/environments/staging/offhours": {
            "enabled": True,
            "sleepAt": "0 19 * * 1-5",
            "wakeAt": "0 8 * * 1-5",
            "timezone": "Europe/Paris",
        },
        "/api/deployments/7/release/settings": {
            "settings": {"strategy": "rolling", "webhook": LEAK["message"]}
        },
        "/api/projects/demo/imports": {
            "imports": [
                {
                    "id": "src-1",
                    "repo_full_name": "org/shop",
                    "branch": "main",
                    "runs": [RUN],
                    "services": [LEAK],
                }
            ]
        },
        "/api/projects/demo/imports/runs/run-1": {
            **RUN,
            "plan": PLAN,
            "services": [],
            "capacity": {},
        },
        "/api/finops/recommendations": [
            {
                "id": "r1",
                "title": "Smaller pods",
                "app_name": "shop",
                "monthly_saving_eur": 3,
                **LEAK,
            }
        ],
    }
    return exact.get(path, {**LEAK, "items": [LEAK]})


def _write(method: str, path: str):
    if path == "/api/projects/demo/imports":
        return {"run": {**RUN, "status": "queued"}}
    if path == "/api/issue-reports":
        return {"id": 1, "duplicate": False, "remaining_today": 9}
    if path == "/api/deployments/":
        return {**APP, "id": 8, "name": "site"}
    if path.startswith("/api/deployments/7/release/"):
        return RELEASE
    if method == "DELETE" and path == "/api/deployments/7":
        return None
    return {**LEAK, "status": "accepted"}


class LeakyCmp:
    """The CMP API, every answer laced with secrets; records each call."""

    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return _get(path) if method == "GET" else _write(method, path)

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def cmp(monkeypatch):
    api = LeakyCmp()
    caller = SimpleNamespace(
        api=api,
        require_write=mock.Mock(),
        require_role=mock.AsyncMock(),
        require_cluster_up=mock.AsyncMock(),
        username="alice",
        grant_id=1,
        scopes=store.SCOPES,
        claims={"name": "Alice", "cnp_agent": {"client": "Claude Code"}},
    )
    for module in (mcp_server, ops, mcp_ai):
        monkeypatch.setattr(module, "Caller", lambda: caller)
    sandbox = policy.ProjectStake("demo", "sandbox", None)
    monkeypatch.setattr(policy, "project_stake", lambda _project: sandbox)
    monkeypatch.setattr(policy, "set_project_stake", mock.Mock())
    monkeypatch.setattr(
        policy, "app_has_firing_alert", mock.AsyncMock(return_value=False)
    )
    confirmations = []

    def request_confirmation(*args):
        confirmations.append(args)
        return SimpleNamespace(
            id="c-1", expires_at=datetime(2026, 11, 19, tzinfo=timezone.utc)
        )

    monkeypatch.setattr(policy, "request_confirmation", request_confirmation)
    monkeypatch.setattr(
        mcp_server.notifications, "notify_project", mock.Mock()
    )
    monkeypatch.setattr(mcp_server.audit, "record_agent_event", mock.Mock())
    monkeypatch.setattr(mcp_server.settings, "STEP_UP_ENABLED", False)
    monkeypatch.setattr(
        mcp_server,
        "_repo_token",
        mock.AsyncMock(return_value=("installation", "org/shop")),
    )
    monkeypatch.setattr(
        mcp_server,
        "get_file_content",
        mock.AsyncMock(
            return_value=(f'DB_PASSWORD = "{DB_PASSWORD}"\n', "sha")
        ),
    )
    repo = {
        "commit_files": mock.AsyncMock(
            return_value=SimpleNamespace(
                sha="f" * 40, branch="main", pull_request_url=None
            )
        ),
        "create_tag": mock.AsyncMock(return_value="abc1234def"),
        "list_files": mock.AsyncMock(return_value=["config.py", LOG_LINE]),
    }
    for name, fake in repo.items():
        monkeypatch.setattr(repo_ops, name, fake)
    monkeypatch.setattr(
        skills,
        "list_skills",
        mock.AsyncMock(
            return_value=[skills.Skill("cnp-platform", LOG_LINE, [])]
        ),
    )
    monkeypatch.setattr(
        skills, "read", mock.AsyncMock(return_value=f"# Guide\n{LOG_LINE}")
    )
    monkeypatch.setattr(
        mcp_server, "cluster_states", mock.AsyncMock(return_value={})
    )
    api.confirmations = confirmations
    api.repo = repo
    return api


# Each write tool with arguments that apply on a sandbox project.
WRITE_CALLS = {
    "create_project": {"name": "lab", "user_confirmed": True},
    "create_app": {"project": "demo", "name": "site"},
    "delete_app": {"app_id": 7, "user_confirmed": True},
    "push_files": {
        "app_id": 7,
        "files": {"index.html": "<h1>hi</h1>"},
        "message": "Say hi",
    },
    "cut_release": {"app_id": 7, "bump": "patch"},
    "release_action": {"app_id": 7, "action": "promote"},
    "analyze_repo": {"project": "demo", "repo_full_name": "org/shop"},
    "import_repo": {"project": "demo", "run_id": "run-1"},
    "link_apps": {
        "project": "demo",
        "from_app": "front",
        "to_app": "api",
        "port": 8000,
    },
    "unlink_apps": {"project": "demo", "from_app": "front", "to_app": "api"},
    "app_power": {"app_id": 7, "action": "restart"},
    "update_app_config": {"app_id": 7, "changes": {"replicaCount": 2}},
    "staging": {"app_id": 7, "action": "schedule", "sleep_at": "0 20 * * *"},
    "promotion": {"app_id": 7, "action": "cancel", "promotion_id": 1},
    "set_promotion_policy": {"app_id": 7, "soak_minutes": 30},
    "ci_run": {"app_id": 7, "action": "start", "suite": "smoke"},
    "release_traffic": {"app_id": 7, "action": "start"},
    "update_release_settings": {
        "app_id": 7,
        "changes": {"strategy": "canary"},
    },
    "project_member": {
        "project": "demo",
        "action": "add",
        "username": "bob",
        "user_confirmed": True,
    },
    "delete_project": {"project": "demo", "user_confirmed": True},
    "set_budget": {"project": "demo", "monthly_amount_eur": 50},
    "cost_recommendation": {
        "project": "demo",
        "recommendation_id": "r1",
        "action": "ignore",
    },
    "security_scan": {"project": "demo", "app": "shop"},
    "request_security_exception": {
        "project": "demo",
        "fingerprint": "f1",
        "status": "accepted_risk",
        "statement": "Fixed upstream next month",
        "user_confirmed": True,
    },
    "database_backup": {"project": "demo", "app": "shop"},
    "report_issue": {
        "category": "error",
        "summary": "get_app answers 500 on every app",
        "steps": LOG_LINE,
        "user_confirmed": True,
    },
    "set_project_ai": {
        "project": "demo",
        "enabled": True,
        "models": ["claude-haiku-5-5"],
    },
    "enable_app_ai": {"project": "demo", "app": "shop"},
    "create_agent": {
        "project": "demo",
        "template": "assistant",
        "name": "helper",
        "repo_app": "shop",
        "instructions": "Answer questions.",
    },
    "update_agent": {
        "project": "demo",
        "agent": "helper",
        "changes": {"instructions": "Be brief."},
    },
    "suspend_agent": {"project": "demo", "agent": "helper"},
    "delete_agent": {
        "project": "demo",
        "agent": "helper",
        "user_confirmed": True,
    },
}
# A person approves or applies these in the CMP: the call never applies.
APPROVED_IN_CMP = {"import_repo", "link_apps", "unlink_apps"}
# Its dry_run (true by default) is its preview; request_secret only answers
# with a link.
NO_PREVIEW = {"database_restore", "request_secret"}
READ_CALLS = {
    "whoami": {},
    "get_guide": {"name": "cnp-platform"},
    "get_project_policy": {"project": "demo"},
    "list_apps": {"project": "demo"},
    "get_app": {"app_id": 7},
    "wait_for_app": {"app_id": 7, "timeout_seconds": 5},
    "list_app_files": {"app_id": 7},
    "read_app_file": {"app_id": 7, "path": "config.py"},
    "request_secret": {"app_id": 7, "name": "STRIPE_KEY", "purpose": "pay"},
    "get_release": {"app_id": 7},
    "list_releases": {"app_id": 7},
    "get_app_logs": {"app_id": 7},
    "get_app_metrics": {"app_id": 7},
    "query_app_logs": {"app_id": 7, "mode": "search", "search": "login"},
    "list_app_traces": {"app_id": 7},
    "get_alerts": {"app_id": 7},
    "get_slo": {"app_id": 7},
    "get_import": {"project": "demo", "run_id": "run-1"},
    "get_topology": {"project": "demo"},
    "get_app_config": {"app_id": 7},
    "get_environments": {"app_id": 7},
    "list_members": {"project": "demo"},
    "get_costs": {"project": "demo"},
    "get_security": {"project": "demo"},
    "list_backups": {"project": "demo", "app": "shop"},
    "get_alert_rules": {"project": "demo"},
    "get_project_ai": {"project": "demo"},
    "get_ai_usage": {"project": "demo"},
    "list_agent_templates": {},
    "list_agents": {"project": "demo"},
    "get_agent": {"project": "demo", "agent": "helper"},
    "database_restore": {"project": "demo", "app": "shop", "backup": "b-1"},
}
# Same tools, other branches worth a pass with secrets in the answers.
MORE_CALLS = [
    ("get_import", {"project": "demo"}),
    ("query_app_logs", {"app_id": 7}),
    *(
        (name, {**arguments, "preview": True})
        for name, arguments in WRITE_CALLS.items()
    ),
]


def _tools():
    return {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}


def _call(name: str, arguments: dict) -> dict:
    return asyncio.run(_tools()[name].run(arguments))


def _as_the_model_sees_it(name: str, arguments: dict) -> str:
    result = asyncio.run(_tools()[name].run(arguments, convert_result=True))
    return json.dumps(jsonable_encoder(result))


def _write_tool_names() -> set[str]:
    return {
        name
        for name, tool in _tools().items()
        if not (tool.annotations and tool.annotations.readOnlyHint)
    }


# --- preview ----------------------------------------------------------------


def test_every_write_tool_offers_a_preview():
    without = {
        name
        for name in _write_tool_names() - NO_PREVIEW
        if "preview" not in _tools()[name].parameters["properties"]
    }

    assert not without


def test_every_write_tool_has_a_preview_case_here():
    assert set(WRITE_CALLS) == _write_tool_names() - NO_PREVIEW


@pytest.mark.parametrize("tool", sorted(WRITE_CALLS))
def test_a_preview_changes_nothing_and_asks_nobody(cmp, tool):
    answer = _call(tool, {**WRITE_CALLS[tool], "preview": True})

    assert (
        answer["preview"],
        cmp.writes(),
        cmp.confirmations,
        cmp.repo["commit_files"].await_count,
        cmp.repo["create_tag"].await_count,
    ) == (True, [], [], 0, 0)


# report_issue has no stake policy: test_issue_reports covers its preview.
@pytest.mark.parametrize(
    "tool", sorted(set(WRITE_CALLS) - APPROVED_IN_CMP - {"report_issue"})
)
def test_applying_runs_the_call_the_preview_showed(cmp, tool, monkeypatch):
    previewed = _call(tool, {**WRITE_CALLS[tool], "preview": True})
    gate = mock.AsyncMock(wraps=mcp_server._gate)
    monkeypatch.setattr(mcp_server, "_gate", gate)
    monkeypatch.setattr(ops, "_gate", gate)

    applied = _call(tool, WRITE_CALLS[tool])

    assert (applied["ok"], gate.await_args.kwargs["arguments"]) == (
        True,
        previewed["arguments"],
    )


@pytest.mark.parametrize("tool", sorted(APPROVED_IN_CMP))
def test_the_approval_asked_covers_the_call_the_preview_showed(cmp, tool):
    previewed = _call(tool, {**WRITE_CALLS[tool], "preview": True})

    _call(tool, WRITE_CALLS[tool])

    asked_arguments = cmp.confirmations[0][5]
    assert asked_arguments == previewed["arguments"]


def test_a_config_preview_shows_each_value_before_and_after(cmp):
    answer = _call(
        "update_app_config",
        {"app_id": 7, "changes": {"replicaCount": 2}, "preview": True},
    )

    assert answer["changes"] == [
        {"path": "replicaCount", "before": 1, "after": 2}
    ]


def test_applying_a_config_sends_the_changes_the_preview_listed(cmp):
    previewed = _call(
        "update_app_config",
        {"app_id": 7, "changes": {"replicaCount": 2}, "preview": True},
    )

    _call("update_app_config", {"app_id": 7, "changes": {"replicaCount": 2}})

    sent = cmp.writes()[0][2]["json"]
    assert {c["path"]: c["after"] for c in previewed["changes"]} == {
        k: v for k, v in sent.items() if k != "_sha"
    }


def test_a_schedule_preview_keeps_the_hours_not_given(cmp):
    answer = _call(
        "staging",
        {
            "app_id": 7,
            "action": "schedule",
            "sleep_at": "0 20 * * *",
            "preview": True,
        },
    )

    assert answer["changes"] == [
        {"path": "sleepAt", "before": "0 19 * * 1-5", "after": "0 20 * * *"}
    ]


def test_a_push_preview_shows_the_diff_against_main(cmp):
    answer = _call(
        "push_files",
        {
            "app_id": 7,
            "files": {"config.py": "DEBUG = True\n"},
            "message": "Debug",
            "preview": True,
        },
    )

    diff = answer["changes"]["files"]["config.py"]
    assert "+DEBUG = True" in diff and "-DB_PASSWORD" in diff


def test_a_preview_says_what_applying_will_take(cmp):
    answer = _call(
        "delete_app", {"app_id": 7, "preview": True, "user_confirmed": False}
    )

    assert answer["applying_needs"] == mcp_server.APPLYING_NEEDS["ask"]


def test_a_preview_of_a_refused_action_says_a_person_does_it(cmp, monkeypatch):
    standard = policy.ProjectStake("demo", "standard", None)
    monkeypatch.setattr(policy, "project_stake", lambda _project: standard)

    answer = _call("delete_app", {"app_id": 7, "preview": True})

    assert (answer["preview"], answer["applying_needs"]) == (
        True,
        mcp_server.APPLYING_NEEDS["deny"],
    )


# --- secret masking ---------------------------------------------------------


def test_every_tool_is_called_with_secrets_here():
    assert set(WRITE_CALLS) | set(READ_CALLS) == set(_tools())


@pytest.mark.parametrize(
    "tool,arguments",
    [*WRITE_CALLS.items(), *READ_CALLS.items(), *MORE_CALLS],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_no_tool_answer_carries_a_secret_value(cmp, tool, arguments):
    seen = _as_the_model_sees_it(tool, arguments)

    assert [s for s in KNOWN_SECRETS if s in seen] == []


def test_an_api_error_carrying_a_secret_reaches_the_model_masked(
    cmp, monkeypatch
):
    async def failing(method, path, **kwargs):
        raise internal_api.ApiError(500, f"vault said {VAULT_TOKEN}")

    cmp_caller = mcp_server.Caller()
    monkeypatch.setattr(cmp_caller, "api", failing)

    answer = _call("get_app", {"app_id": 7})

    assert answer == {
        "ok": False,
        "status": 500,
        "error": f"vault said {MASK}",
    }


def test_a_secret_given_by_the_assistant_is_not_echoed_back(cmp):
    answer = _call(
        "update_app_config",
        {
            "app_id": 7,
            "changes": {"env": {"STRIPE_KEY": STRIPE_KEY}},
            "preview": True,
        },
    )

    assert STRIPE_KEY not in json.dumps(answer)


# --- the filter itself: secrets out, the rest readable ------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (
            f"postgresql://app:{DB_PASSWORD}@db:5432/app",
            f"postgresql://app:{MASK}@db:5432/app",
        ),
        (f"export VAULT_TOKEN={VAULT_TOKEN}", f"export VAULT_TOKEN={MASK}"),
        (f"Authorization: Bearer {JWT}", f"Authorization: Bearer {MASK}"),
        ({"client_secret": "abc"}, {"client_secret": MASK}),
        (
            {"name": "SMTP_PASSWORD", "value": "abc"},
            {"name": "SMTP_PASSWORD", "value": MASK},
        ),
        (PRIVATE_KEY, MASK),
    ],
)
def test_secret_values_are_masked(value, expected):
    assert mask(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        {"name": "API_TOKEN", "value": None, "secret": True},
        {"kind": "hardcoded_password", "file": "compose.yml", "line": 3},
        {"url": "https://shop.apps.3istor.com/login?next=/"},
        {"image": "ghcr.io/3-istor/shop:v1.2.0", "commit": "a" * 40},
        {"name": "LOG_LEVEL", "value": "info"},
        {"secret_name": "shop-db", "token_ttl": 3600},
        "Call get_import with run_id run-1 once the token is ready.",
    ],
)
def test_ordinary_values_stay_readable(value):
    assert mask(value) == value
