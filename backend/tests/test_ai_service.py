"""What creating, changing, suspending and deleting agents writes, and where."""

import asyncio
import copy
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.ai import AiAgent
from app.services.agents import policy
from app.services.ai import catalog, gitops, identity, service
from app.services.ai import usage as ai_usage

TEMPLATES = {
    "assistant": {
        "id": "assistant",
        "defaults": {"instructions": "Aide l'équipe.", "maxSteps": 4},
        "pair": None,
    },
    # Same shape as app-templates agents/app-bot.yaml.
    "app-bot": catalog._template(  # pylint: disable=protected-access
        {
            "id": "app-bot",
            "recommended": {
                "models": ["claude-haiku-5-5"],
                "isolation": "gvisor",
                "dailyTokenBudget": 300_000,
            },
            "defaults": {
                "template": "app-bot",
                "description": "Lit l'événement",
                "instructions": "Lis le fil, ne fais rien d'autre.",
                "tools": ["web_search", "run_python"],
                "trigger": {"type": "event"},
                "calls": [],
            },
            "pair": {
                "reader": {"suffix": "reader"},
                "actor": {
                    "suffix": "actor",
                    "recommended": {
                        "isolation": "gvisor",
                        "dailyTokenBudget": 50_000,
                    },
                    "defaults": {
                        "description": "Publie la réponse",
                        "instructions": "Publie la réponse.",
                        "tools": [],
                        "maxSteps": 3,
                        "trigger": {"type": "a2a"},
                    },
                },
            },
        }
    ),
}
TOOLS = [
    {
        "name": "web_search",
        "source": "platform",
        "badges": ["untrusted_content", "external_send"],
    },
    {"name": "run_python", "source": "platform", "badges": ["executes_code"]},
    {
        "name": "shop__get_thread",
        "source": "shop",
        "badges": ["untrusted_content"],
    },
    {"name": "shop__get_order", "source": "shop", "badges": ["private_data"]},
    {
        "name": "shop__reply_as_bot",
        "source": "shop",
        "badges": ["writes", "external_send"],
    },
]


def _record() -> dict:
    return {
        "metadata": {"name": "alpha"},
        "spec": {
            "apps": [
                {
                    "name": "shop",
                    "type": "service",
                    "repoURL": "https://github.com/3-Istor/shop",
                }
            ],
            "ai": {
                "enabled": True,
                "models": ["claude-haiku-5-5"],
                "webSearch": True,
                "dailyTokenBudget": 1_000_000,
            },
        },
    }


class World:
    """Git, Keycloak, Vault and Argo CD as the service sees them."""

    def __init__(self):
        self.record = _record()
        self.commits = []
        self.pulls = []
        self.values = {}
        self.clients = {}
        self.secrets = {}
        self.refreshed = []

    async def commit(self, project, change, message):
        record = copy.deepcopy(self.record)
        await change(record, "head")
        self.record = record
        self.commits.append(message)
        return "sha"

    async def read_record(self, project):
        return copy.deepcopy(self.record)

    async def open_pr(self, repo, files, slug, title, body):
        self.pulls.append((repo, files, title))
        return {
            "number": len(self.pulls),
            "url": f"pr/{len(self.pulls)}",
            "state": "open",
            "repo": repo,
        }

    async def read_values(self, repo, path, ref="main"):
        return self.values.get(path)

    def ensure_client(self, client_id, project, description, scopes):
        self.clients[client_id] = {
            "enabled": True,
            "project": project,
            "scopes": scopes,
        }
        return client_id

    def set_enabled(self, client_id, enabled):
        self.clients[client_id]["enabled"] = enabled

    def delete_client(self, client_id):
        self.clients.pop(client_id, None)

    async def store(self, db, project, app, client_id, secret):
        self.secrets[(project, app)] = client_id

    async def drop(self, db, project, app):
        self.secrets.pop((project, app), None)

    async def refresh(self, project):
        self.refreshed.append(project)


@pytest.fixture
def world(monkeypatch):
    world = World()
    monkeypatch.setattr(service, "commit_record_change", world.commit)
    monkeypatch.setattr(service, "record_of", world.read_record)

    async def cached(project):
        return copy.deepcopy(world.record), "sha"

    monkeypatch.setattr(service.project_registry, "read_record_cached", cached)
    monkeypatch.setattr(
        service.project_registry, "ensure_app_names_free", mock.AsyncMock()
    )
    monkeypatch.setattr(catalog, "tools", mock.AsyncMock(return_value=TOOLS))
    monkeypatch.setattr(
        catalog, "template", mock.AsyncMock(side_effect=TEMPLATES.get)
    )
    monkeypatch.setattr(gitops, "open_pr", world.open_pr)
    monkeypatch.setattr(gitops, "read_values", world.read_values)
    monkeypatch.setattr(
        gitops, "pull_state", mock.AsyncMock(side_effect=lambda p: p)
    )
    monkeypatch.setattr(gitops, "store_client_secret", world.store)
    monkeypatch.setattr(gitops, "drop_client_secret", world.drop)
    monkeypatch.setattr(gitops, "refresh_project", world.refresh)
    monkeypatch.setattr(identity, "ensure_client", world.ensure_client)
    monkeypatch.setattr(identity, "ensure_test_audience", mock.Mock())
    monkeypatch.setattr(identity, "drop_test_audience", mock.Mock())
    monkeypatch.setattr(identity, "client_secret", lambda _c: "s3cret")
    monkeypatch.setattr(identity, "set_enabled", world.set_enabled)
    monkeypatch.setattr(identity, "delete_client", world.delete_client)
    monkeypatch.setattr(
        identity,
        "is_enabled",
        lambda c: world.clients.get(c, {}).get("enabled"),
    )
    monkeypatch.setattr(ai_usage, "usage", mock.AsyncMock(return_value=[]))
    monkeypatch.setattr(
        ai_usage,
        "budget",
        mock.AsyncMock(
            return_value={"spend_usd": 1, "budget_usd": 5, "status": "ok"}
        ),
    )
    monkeypatch.setattr(
        service.gateway, "reachable", mock.AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        policy,
        "project_stake",
        lambda project: policy.ProjectStake(project, "standard", None),
    )
    return world


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def run(coro):
    return asyncio.run(coro)


APP_BOT = {
    "template": "app-bot",
    "name": "bot",
    "repo_app": "shop",
    "description": "Répond aux mentions",
    "tools": ["shop__get_thread", "web_search", "run_python"],
    "callers": ["app:shop"],
    "actor": {"tools": ["shop__reply_as_bot"]},
}


def test_creating_an_app_bot_declares_a_reader_and_an_actor(world, db):
    run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))

    rights = {a["name"]: a for a in world.record["spec"]["ai"]["agents"]}
    assert rights == {
        "bot-reader": {
            "name": "bot-reader",
            "owner": "alice",
            "models": ["claude-haiku-5-5"],
            "dailyTokenBudget": 300_000,
            "tools": ["shop__get_thread", "web_search", "run_python"],
            "isolation": "gvisor",
            "callers": ["app:shop"],
            "calls": ["bot-actor"],
            "status": "active",
        },
        "bot-actor": {
            "name": "bot-actor",
            "owner": "alice",
            "models": ["claude-haiku-5-5"],
            "dailyTokenBudget": 50_000,
            "tools": ["shop__reply_as_bot"],
            "isolation": "gvisor",
            "callers": ["agent:bot-reader"],
            "calls": [],
            "status": "active",
        },
    }


def test_each_agent_becomes_an_app_of_type_agent_on_the_same_repository(
    world, db
):
    run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))

    assert [
        a for a in world.record["spec"]["apps"] if a["type"] == "agent"
    ] == [
        {
            "name": n,
            "type": "agent",
            "repoURL": "https://github.com/3-Istor/shop",
        }
        for n in ("bot-reader", "bot-actor")
    ]


def test_one_pull_request_carries_both_values_files(world, db):
    result = run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))

    repo, files, _ = world.pulls[0]
    assert (repo, sorted(files), result["pull_request"]["url"]) == (
        "3-Istor/shop",
        ["deploy/values-bot-actor.yaml", "deploy/values-bot-reader.yaml"],
        "pr/1",
    )
    assert "trigger:\n    type: a2a" in files["deploy/values-bot-actor.yaml"]


def test_each_agent_gets_its_client_and_its_secret(world, db):
    run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))

    assert (sorted(world.clients), sorted(world.secrets)) == (
        ["agent-alpha-bot-actor", "agent-alpha-bot-reader"],
        [("alpha", "bot-actor"), ("alpha", "bot-reader")],
    )
    assert world.clients["agent-alpha-bot-actor"]["scopes"] == [
        "mcp-read",
        "mcp-write",
    ]


def test_the_mirror_lists_the_new_agents_with_their_pull_request(world, db):
    run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))

    rows = db.query(AiAgent).order_by(AiAgent.name).all()
    assert [(r.name, r.role, r.pull_request["number"]) for r in rows] == [
        ("bot-actor", "actor", 1),
        ("bot-reader", "reader", 1),
    ]


def test_three_risks_in_one_agent_write_nothing(world, db):
    body = {
        "template": "assistant",
        "name": "leaky",
        "repo_app": "shop",
        "tools": ["shop__get_order", "shop__get_thread", "web_search"],
    }

    with pytest.raises(service.AiError) as refused:
        run(service.create_agents(db, "alpha", body, "alice"))

    assert (
        refused.value.status,
        world.commits,
        world.clients,
        world.pulls,
    ) == (
        422,
        [],
        {},
        [],
    )
    assert any("Rule of Two" in e for e in refused.value.errors)


def test_an_app_outside_the_org_cannot_host_an_agent(world, db):
    world.record["spec"]["apps"][0][
        "repoURL"
    ] = "https://github.com/someone/shop"

    with pytest.raises(service.AiError) as refused:
        run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))

    assert refused.value.status == 422


def test_suspending_closes_the_client_the_record_and_refreshes_argo(world, db):
    run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))

    summary = run(
        service.set_status(db, "alpha", "bot-reader", "suspended", "bob")
    )

    rights = {
        a["name"]: a["status"] for a in world.record["spec"]["ai"]["agents"]
    }
    assert (
        summary["status"],
        rights["bot-reader"],
        world.clients["agent-alpha-bot-reader"]["enabled"],
        world.refreshed[-1],
    ) == ("suspended", "suspended", False, "alpha")


def test_resuming_reopens_the_client(world, db):
    run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))
    run(service.set_status(db, "alpha", "bot-reader", "suspended", "bob"))

    run(service.set_status(db, "alpha", "bot-reader", "active", "bob"))

    assert world.clients["agent-alpha-bot-reader"]["enabled"] is True


def test_deleting_removes_rights_app_client_secret_and_values(world, db):
    run(service.create_agents(db, "alpha", dict(APP_BOT), "alice"))
    world.values["deploy/values-bot-actor.yaml"] = {"agent": {}}

    result = run(service.delete_agent(db, "alpha", "bot-actor", "bob"))

    spec = world.record["spec"]
    assert (
        [a["name"] for a in spec["ai"]["agents"]],
        spec["ai"]["agents"][0]["calls"],
        [a["name"] for a in spec["apps"]],
        "agent-alpha-bot-actor" in world.clients,
        ("alpha", "bot-actor") in world.secrets,
        world.pulls[-1][1],
        result["deleted"],
    ) == (
        ["bot-reader"],
        [],
        ["shop", "bot-reader"],
        False,
        False,
        {"deploy/values-bot-actor.yaml": None},
        "bot-actor",
    )


def test_an_update_of_the_instructions_is_a_pull_request_only(world, db):
    run(
        service.create_agents(
            db,
            "alpha",
            {
                **APP_BOT,
                "template": "assistant",
                "name": "helper",
                "tools": ["web_search"],
            },
            "alice",
        )
    )
    commits = len(world.commits)

    run(
        service.update_agent(
            db, "alpha", "helper", {"instructions": "Sois bref."}, "alice"
        )
    )

    _, files, title = world.pulls[-1]
    assert (len(world.commits), title) == (
        commits,
        "feat(agents): update helper",
    )
    assert "Sois bref." in files["deploy/values-helper.yaml"]


def test_an_update_of_the_tools_changes_the_rights(world, db):
    run(
        service.create_agents(
            db,
            "alpha",
            {
                **APP_BOT,
                "template": "assistant",
                "name": "helper",
                "tools": ["web_search"],
            },
            "alice",
        )
    )

    run(
        service.update_agent(
            db, "alpha", "helper", {"tools": ["run_python"]}, "alice"
        )
    )

    rights = world.record["spec"]["ai"]["agents"][0]
    assert (rights["tools"], rights["isolation"]) == (["run_python"], "gvisor")


def test_a_critical_project_cannot_turn_ai_on(world, db, monkeypatch):
    monkeypatch.setattr(
        policy,
        "project_stake",
        lambda project: policy.ProjectStake(project, "critical", None),
    )
    body = {
        "enabled": True,
        "models": ["claude-haiku-5-5"],
        "web_search": False,
        "daily_token_budget": 0,
    }

    with pytest.raises(service.AiError) as refused:
        run(service.set_project_ai(db, "alpha", body, "alice"))

    assert (refused.value.status, world.commits) == (409, [])


def test_setting_project_ai_writes_the_record_in_camel_case(world, db):
    body = {
        "enabled": True,
        "models": ["claude-sonnet-5-5"],
        "web_search": False,
        "daily_token_budget": 5000,
    }

    view = run(service.set_project_ai(db, "alpha", body, "alice"))

    assert world.record["spec"]["ai"] == {
        "enabled": True,
        "models": ["claude-sonnet-5-5"],
        "webSearch": False,
        "dailyTokenBudget": 5000,
    }
    assert (
        view["enabled"],
        view["can_enable"],
        view["backend"]["status"],
    ) == (True, True, "ok")


def test_enabling_an_app_writes_client_secret_rights_and_values(world, db):
    world.values["deploy/values-shop.yaml"] = {"replicaCount": 1}

    result = run(
        service.enable_app(
            db,
            "alpha",
            "shop",
            {"enabled": True, "daily_token_budget": 50_000},
            "alice",
        )
    )

    assert (
        result["client_id"],
        result["secret_ref"],
        world.record["spec"]["ai"]["apps"],
        world.secrets[("alpha", "shop")],
    ) == (
        "app-alpha-shop",
        "project-alpha/shop/ai-oidc",
        [{"name": "shop", "enabled": True, "dailyTokenBudget": 50_000}],
        "app-alpha-shop",
    )
    assert (
        "ai:\n  enabled: true" in world.pulls[0][1]["deploy/values-shop.yaml"]
    )


def test_an_app_budget_above_the_project_is_refused(world, db):
    with pytest.raises(service.AiError) as refused:
        run(
            service.enable_app(
                db,
                "alpha",
                "shop",
                {"enabled": True, "daily_token_budget": 5_000_000},
                "alice",
            )
        )

    assert refused.value.status == 422


def test_an_agent_declared_in_yaml_only_shows_up_in_the_list(world, db):
    world.record["spec"]["ai"]["agents"] = [
        {
            "name": "manual",
            "tools": ["web_search"],
            "status": "active",
            "models": ["claude-haiku-5-5"],
        }
    ]

    items = run(service.list_agents(db, "alpha"))

    assert [(i["name"], i["source"], i["risk"]["count"]) for i in items] == [
        ("manual", "yaml", 2)
    ]


def test_turning_ai_on_mirrors_the_stake_level_in_the_record(world, db):
    body = {
        "enabled": True,
        "models": ["claude-haiku-5-5"],
        "web_search": False,
        "daily_token_budget": 0,
    }

    run(service.set_project_ai(db, "alpha", body, "alice"))

    assert world.record["spec"]["stake"] == "standard"


def test_a_public_cloud_project_cannot_turn_ai_on(world, db):
    world.record["spec"]["targetCloud"] = "aws"
    body = {
        "enabled": True,
        "models": ["claude-haiku-5-5"],
        "web_search": False,
        "daily_token_budget": 0,
    }

    with pytest.raises(service.AiError) as refused:
        run(service.set_project_ai(db, "alpha", body, "alice"))

    assert refused.value.status == 409


def test_a_stake_change_is_written_once(world):
    run(service.sync_stake("alpha", "demo"))
    run(service.sync_stake("alpha", "demo"))

    assert (world.record["spec"]["stake"], len(world.commits)) == ("demo", 1)


def test_a_project_with_ai_on_cannot_become_critical(world):
    with pytest.raises(service.AiError) as refused:
        run(service.refuse_if_ai_enabled("alpha"))

    assert refused.value.status == 409
