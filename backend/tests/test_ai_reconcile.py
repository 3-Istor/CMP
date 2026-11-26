"""The AI reconcile against an in-memory Keycloak and Vault: what a record
declares in Git gets its client and secret, and a second run writes nothing."""

import asyncio
import copy
import itertools
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.ai import AiAgent
from app.services import vault_client
from app.services.ai import catalog, gitops, identity, reconcile

PROJECT = "epitweet"
TOOLS = [
    {"name": "web_search", "badges": ["untrusted_content"]},
    {"name": "run_python", "badges": ["executes_code"]},
    {"name": "epitweet__reply", "badges": ["writes", "external_send"]},
]
REALM_SCOPES = ("basic", "profile", "ai-gateway", "mcp-read", "mcp-write")
REALM_SCOPES += ("web-search",)


class FakeKeycloak:
    """The admin REST paths identity uses, enough to observe writes."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.clients: dict[str, dict] = {}
        self.scopes = {f"s-{n}": n for n in REALM_SCOPES}
        self.groups: dict[str, str] = {}
        self.memberships: dict[str, set[str]] = {}
        self.writes: list[tuple[str, str]] = []
        self.add_client(settings.KEYCLOAK_CLIENT_ID, enabled=True)

    def add_client(self, client_id, enabled=True, mappers=None):
        internal = f"c-{next(self.ids)}"
        self.clients[internal] = {
            "id": internal,
            "clientId": client_id,
            "enabled": enabled,
            "protocolMappers": list(mappers or []),
            "default": {"s-basic", "s-profile"},
            "optional": set(),
        }
        return internal

    def by_client_id(self, client_id):
        return next(
            (c for c in self.clients.values() if c["clientId"] == client_id),
            None,
        )

    def _view(self, client):
        return {
            k: copy.deepcopy(v)
            for k, v in client.items()
            if k not in ("default", "optional")
        }

    def _scopes(self, ids):
        return [{"id": i, "name": self.scopes[i]} for i in sorted(ids)]

    def __call__(self, method, path, json=None):
        if method != "GET":
            self.writes.append((method, path))
        route, _, query = path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if p)
        parts = route.strip("/").split("/")
        handler = getattr(
            self, f"_{method.lower()}_{parts[0]}".replace("-", "_")
        )
        return handler(parts[1:], params, json)

    def _get_clients(self, rest, params, _json):
        if not rest:
            wanted = params["clientId"]
            if params.get("search") == "true":
                found = [
                    c for c in self.clients.values() if wanted in c["clientId"]
                ]
                first, size = int(params["first"]), int(params["max"])
                return [self._view(c) for c in found[first : first + size]]
            return [
                self._view(c)
                for c in self.clients.values()
                if c["clientId"] == wanted
            ]
        client = self.clients[rest[0]]
        if rest[1:] == ["default-client-scopes"]:
            return self._scopes(client["default"])
        if rest[1:] == ["optional-client-scopes"]:
            return self._scopes(client["optional"])
        if rest[1:] == ["service-account-user"]:
            return {"id": f"u-{client['id']}"}
        if rest[1:] == ["client-secret"]:
            return {"value": f"secret-of-{client['clientId']}"}
        raise AssertionError(rest)

    def _post_clients(self, rest, _params, json):
        if not rest:
            self.add_client(
                json["clientId"], json["enabled"], json["protocolMappers"]
            )
        else:
            self.clients[rest[0]]["protocolMappers"].append(json)

    def _put_clients(self, rest, _params, json):
        client = self.clients[rest[0]]
        if len(rest) == 1:
            client["enabled"] = json["enabled"]
        elif rest[1] == "default-client-scopes":
            client["default"].add(rest[2])
        else:
            client["optional"].add(rest[2])

    def _delete_clients(self, rest, _params, _json):
        if len(rest) == 1:
            del self.clients[rest[0]]
        elif rest[1] == "default-client-scopes":
            self.clients[rest[0]]["default"].discard(rest[2])
        else:
            self.clients[rest[0]]["optional"].discard(rest[2])

    def _get_client_scopes(self, _rest, _params, _json):
        return self._scopes(self.scopes)

    def _post_client_scopes(self, _rest, _params, json):
        self.scopes[f"s-{json['name']}"] = json["name"]

    def _delete_client_scopes(self, rest, _params, _json):
        del self.scopes[rest[0]]

    def _get_group_by_path(self, rest, _params, _json):
        path = "/" + "/".join(rest)
        if path not in self.groups:
            raise identity.IdentityError(404, "no group")
        return {"id": self.groups[path], "path": path}

    def _post_groups(self, rest, _params, json):
        parent = next(
            (p for p, i in self.groups.items() if rest and i == rest[0]), ""
        )
        self.groups[f"{parent}/{json['name']}"] = f"g-{next(self.ids)}"

    def _get_users(self, rest, _params, _json):
        paths = {i: p for p, i in self.groups.items()}
        return [
            {"path": paths[g]} for g in self.memberships.get(rest[0], set())
        ]

    def _put_users(self, rest, _params, _json):
        self.memberships.setdefault(rest[0], set()).add(rest[2])

    def client_scopes(self, client_id):
        client = self.by_client_id(client_id)
        return {self.scopes[s] for s in client["default"]}


class FakeVault:
    def __init__(self):
        self.kv: dict[tuple[str, str], dict] = {}
        self.roles: set[tuple[str, str]] = set()
        self.writes: list[str] = []

    def install(self, monkeypatch):
        async def read_kv(mount, path):
            return dict(self.kv.get((mount, path), {}))

        async def write_kv(mount, path, data):
            self.writes.append(f"kv {mount}/{path}")
            self.kv[(mount, path)] = dict(data)

        async def delete_kv(mount, path):
            self.writes.append(f"rm {mount}/{path}")
            self.kv.pop((mount, path), None)

        async def role_exists(mount, name):
            return (mount, name) in self.roles

        async def write_role(mount, name, _policy):
            self.writes.append(f"role {name}")
            self.roles.add((mount, name))

        async def delete_role(mount, name):
            self.writes.append(f"rm role {name}")
            self.roles.discard((mount, name))

        async def policy(*_args):
            self.writes.append("policy")

        for name, fake in {
            "read_kv": read_kv,
            "write_kv": write_kv,
            "delete_kv": delete_kv,
            "app_role_exists": role_exists,
            "write_app_role": write_role,
            "delete_app_role": delete_role,
            "write_policy": policy,
            "delete_policy": policy,
        }.items():
            monkeypatch.setattr(vault_client, name, fake)


@pytest.fixture
def kc(monkeypatch):
    fake = FakeKeycloak()
    monkeypatch.setattr(identity, "_admin", fake)
    monkeypatch.setattr(catalog, "tools", mock.AsyncMock(return_value=TOOLS))
    return fake


@pytest.fixture
def vault(monkeypatch):
    fake = FakeVault()
    fake.install(monkeypatch)
    return fake


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


def _record(agents=(), apps=(), project=PROJECT):
    return {
        "metadata": {"name": project},
        "spec": {
            "apps": [{"name": "epitweet-mcp", "type": "web"}]
            + [{"name": a["name"], "type": "agent"} for a in agents],
            "ai": {
                "enabled": True,
                "agents": list(agents),
                "apps": list(apps),
            },
        },
    }


def _agent(name="grok-reader", status="active", tools=None):
    return {
        "name": name,
        "status": status,
        "tools": ["web_search", "epitweet__reply"] if tools is None else tools,
        "models": ["claude-haiku-5-5"],
    }


def _run(db, record, prune=False, project=PROJECT):
    return asyncio.run(
        reconcile.reconcile_project(db, project, record, prune=prune)
    )


def test_an_agent_declared_only_in_git_gets_its_client_and_secret(
    kc, vault, db
):
    _run(db, _record([_agent()]))

    client = kc.by_client_id("agent-epitweet-grok-reader")
    assert (
        client["enabled"],
        vault.kv[("project-epitweet", "grok-reader/prod/ai-oidc")],
    ) == (
        True,
        {
            "client_id": "agent-epitweet-grok-reader",
            "client_secret": "secret-of-agent-epitweet-grok-reader",
        },
    )


def test_the_client_gets_the_scopes_its_tools_need(kc, vault, db):
    _run(db, _record([_agent()]))

    assert kc.client_scopes("agent-epitweet-grok-reader") >= {
        "ai-gateway",
        "mcp-read",
        "mcp-write",
        "web-search",
    }


def test_a_second_run_writes_nothing(kc, vault, db):
    record = _record(
        [_agent(), _agent("grok-actor", "suspended", [])],
        [{"name": "epitweet-mcp", "enabled": True, "dailyTokenBudget": 0}],
    )
    _run(db, record)
    kc.writes.clear()
    vault.writes.clear()

    report = _run(db, record)

    assert (report["changed"], kc.writes, vault.writes) == (False, [], [])


def test_suspending_in_git_disables_the_client(kc, vault, db):
    _run(db, _record([_agent()]))

    report = _run(db, _record([_agent(status="suspended")]))

    assert (
        kc.by_client_id("agent-epitweet-grok-reader")["enabled"],
        report["items"][0]["changes"],
    ) == (False, ["disabled"])


def test_a_suspended_agent_seen_first_gets_a_disabled_client(kc, vault, db):
    _run(db, _record([_agent(status="suspended")]))

    assert kc.by_client_id("agent-epitweet-grok-reader")["enabled"] is False


def test_a_right_taken_away_in_git_leaves_the_client(kc, vault, db):
    _run(db, _record([_agent()]))

    _run(db, _record([_agent(tools=["web_search"])]))

    assert "mcp-write" not in kc.client_scopes("agent-epitweet-grok-reader")


def test_an_app_with_ai_on_gets_its_client_and_secret(kc, vault, db):
    _run(db, _record(apps=[{"name": "epitweet-mcp", "enabled": True}]))

    assert vault.kv[("project-epitweet", "epitweet-mcp/prod/ai-oidc")][
        "client_id"
    ] == ("app-epitweet-epitweet-mcp")


def test_an_app_with_ai_off_gets_no_client(kc, vault, db):
    _run(db, _record(apps=[{"name": "epitweet-mcp", "enabled": False}]))

    assert kc.by_client_id("app-epitweet-epitweet-mcp") is None


def test_turning_an_app_off_in_git_disables_its_client(kc, vault, db):
    _run(db, _record(apps=[{"name": "epitweet-mcp", "enabled": True}]))

    _run(db, _record(apps=[{"name": "epitweet-mcp", "enabled": False}]))

    assert kc.by_client_id("app-epitweet-epitweet-mcp")["enabled"] is False


def test_an_orphan_client_is_reported_and_kept_by_default(kc, vault, db):
    _run(db, _record([_agent()]))

    report = _run(db, _record())

    assert (
        report["orphans"],
        kc.by_client_id("agent-epitweet-grok-reader") is not None,
    ) == (["agent-epitweet-grok-reader"], True)


def test_prune_deletes_the_orphan_client_and_its_secret(kc, vault, db):
    _run(db, _record([_agent()]))

    report = _run(db, _record(), prune=True)

    assert (
        report["deleted"],
        kc.by_client_id("agent-epitweet-grok-reader"),
        ("project-epitweet", "grok-reader/prod/ai-oidc") in vault.kv,
    ) == (["agent-epitweet-grok-reader"], None, False)


def test_a_client_of_a_project_sharing_the_prefix_is_no_orphan(kc, vault, db):
    other = _record([_agent("bot")], project="epitweet-lab")
    _run(db, other, project="epitweet-lab")

    report = _run(db, _record(), prune=True)

    assert (report["orphans"], report["deleted"]) == ([], [])


def test_credentials_show_up_in_the_mirror_once_the_reconcile_ran(
    kc, vault, db
):
    _run(db, _record([_agent()]))

    row = db.query(AiAgent).filter_by(name="grok-reader").one()
    assert row.credentials == "ok"


def test_a_reconcile_reads_the_agent_settings_into_the_mirror(
    kc, vault, db, monkeypatch
):
    record = _record([_agent()])
    record["spec"]["apps"][-1][
        "repoURL"
    ] = f"https://github.com/{settings.GITHUB_ORG}/epitweet"
    read_values = mock.AsyncMock(
        return_value={
            "agent": {"template": "app-bot", "trigger": {"type": "event"}}
        }
    )
    monkeypatch.setattr(gitops, "read_values", read_values)

    _run(db, record)

    row = db.query(AiAgent).filter_by(name="grok-reader").one()
    assert (row.template, row.declaration["trigger"], row.settings_status) == (
        "app-bot",
        {"type": "event"},
        "ok",
    )
    read_values.assert_awaited_once_with(
        f"{settings.GITHUB_ORG}/epitweet", "deploy/values-grok-reader.yaml"
    )


def test_a_record_write_asks_for_a_reconcile_on_the_app_loop(monkeypatch):
    seen = []

    async def quietly(project, record=None):
        seen.append((project, record))

    async def main():
        reconcile._runs.loop = (
            asyncio.get_running_loop()
        )  # pylint: disable=protected-access
        reconcile.request(PROJECT)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    monkeypatch.setattr(reconcile, "_quietly", quietly)
    try:
        asyncio.run(main())
    finally:
        reconcile._runs.loop = None  # pylint: disable=protected-access

    assert seen == [(PROJECT, None)]
