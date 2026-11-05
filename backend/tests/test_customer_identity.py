import copy
import re
from unittest import mock

import pytest

from app.services import customer_identity as ci
from app.services.customer_identity import (
    APP_ADMIN_ROLES,
    CustomerIdentityError,
)

KC_ROLES = (*APP_ADMIN_ROLES, "manage-realm", "view-realm", "realm-admin")


class FakeKeycloak:
    """In-memory admin API: just the endpoints `apply` is allowed to use."""

    def __init__(self):
        self.realms = {}
        self.writes = []

    def add_realm(self, name, clients=(), **rep):
        rm = f"{name}:realm-management"
        self.realms[name] = {
            "rep": {
                "registrationAllowed": False,
                "organizationsEnabled": False,
                **rep,
            },
            "groups": [],
            "default_groups": [],
            "scopes": {"organization": f"{name}:scope-organization"},
            "roles": {r: f"{rm}:{r}" for r in KC_ROLES},
            "mappings": {},
            "clients": [
                self._client(name, c)
                for c in ("realm-management", "account", *clients)
            ],
        }

    @staticmethod
    def _client(realm, client_id):
        return {
            "id": f"{realm}:{client_id}",
            "clientId": client_id,
            "serviceAccountsEnabled": False,
            "webOrigins": [],
            "defaultClientScopes": ["profile"],
            # Keycloak 26 registers `organization` as an optional scope.
            "optionalClientScopes": ["organization", "phone"],
        }

    def __call__(self, method, path, json=None):
        m = re.fullmatch(r"/admin/realms/([^/]+)(/.*)?", path)
        realm, rest = m.group(1), m.group(2) or ""
        if realm not in self.realms:
            raise CustomerIdentityError(404, f"{path}: no such realm")
        r = self.realms[realm]
        if method != "GET":
            self.writes.append((method, path))
        parts = rest.strip("/").split("/") if rest else []

        def at(verb, *pattern):
            return (
                method == verb
                and len(parts) == len(pattern)
                and all(
                    want in ("*", got) for want, got in zip(pattern, parts)
                )
            )

        if at("GET"):
            return dict(r["rep"])
        if at("PUT"):
            r["rep"].update(json)
        elif at("GET", "groups"):
            return [dict(g) for g in r["groups"]]
        elif at("POST", "groups"):
            r["groups"].append({"id": f"{realm}:g-{json['name']}", **json})
        elif at("GET", "default-groups"):
            return [g for g in r["groups"] if g["id"] in r["default_groups"]]
        elif at("PUT", "default-groups", "*"):
            r["default_groups"].append(rest.rsplit("/", 1)[1])
        elif at("GET", "clients"):
            return copy.deepcopy(r["clients"])
        elif at("PUT", "clients", "*"):
            c = self._client_by_id(r, rest.split("/")[2])
            # Like Keycloak: a PUT without webOrigins derives them from the
            # redirect URIs, silently widening CORS.
            if not c["webOrigins"] and "webOrigins" not in json:
                c["webOrigins"] = ["https://app.example.test"]
            c.update(json)
        elif at("GET", "client-scopes"):
            return [{"id": i, "name": n} for n, i in r["scopes"].items()]
        elif at("GET", "clients", "*", "service-account-user"):
            c = self._client_by_id(r, rest.split("/")[2])
            if not c["serviceAccountsEnabled"]:
                raise CustomerIdentityError(400, "no service account")
            return {"id": f"sa:{c['id']}"}
        elif at("GET", "clients", "*", "roles"):
            assert rest.split("/")[2] == f"{realm}:realm-management"
            return [{"id": i, "name": n} for n, i in r["roles"].items()]
        elif at("GET", "users", "*", "role-mappings", "clients", "*"):
            uid = rest.split("/")[2]
            return [
                {"id": r["roles"][n], "name": n}
                for n in sorted(r["mappings"].get(uid, ()))
            ]
        elif at("POST", "users", "*", "role-mappings", "clients", "*"):
            uid = rest.split("/")[2]
            assert rest.endswith(f"/{realm}:realm-management")
            for role in json:
                assert role["id"] == r["roles"][role["name"]], "foreign role"
                r["mappings"].setdefault(uid, set()).add(role["name"])
        elif at("DELETE", "clients", "*", "optional-client-scopes", "*"):
            c = self._client_by_id(r, rest.split("/")[2])
            c["optionalClientScopes"].remove(self._scope_name(r, rest))
        elif at("PUT", "clients", "*", "default-client-scopes", "*"):
            # Like Keycloak: a scope already optional is NOT moved.
            c = self._client_by_id(r, rest.split("/")[2])
            name = self._scope_name(r, rest)
            if name not in c["optionalClientScopes"]:
                c["defaultClientScopes"].append(name)
        else:
            raise AssertionError(f"unexpected call {method} {path}")

    @staticmethod
    def _client_by_id(realm, client_id):
        return next(c for c in realm["clients"] if c["id"] == client_id)

    @staticmethod
    def _scope_name(realm, rest):
        scope_id = rest.rsplit("/", 1)[1]
        return next(n for n, i in realm["scopes"].items() if i == scope_id)

    def client(self, realm, client_id):
        return next(
            c
            for c in self.realms[realm]["clients"]
            if c["clientId"] == client_id
        )

    def roles_of(self, realm, client_id):
        uid = f"sa:{realm}:{client_id}"
        return self.realms[realm]["mappings"].get(uid, set())


@pytest.fixture
def kc():
    fake = FakeKeycloak()
    fake.add_realm("p", ["cnp-p-web", "cnp-other-x"])
    fake.add_realm("p-staging", ["cnp-p-web-stg"], registrationAllowed=True)
    with mock.patch.object(ci, "_admin", fake):
        yield fake


def test_enabling_opens_sign_up_and_organizations(kc):
    ci.apply("p", True)

    assert kc.realms["p"]["rep"]["registrationAllowed"] is True
    assert kc.realms["p"]["rep"]["organizationsEnabled"] is True
    assert kc.realms["p-staging"]["rep"]["organizationsEnabled"] is True


def test_sign_ups_land_in_users(kc):
    ci.apply("p", True)
    ci.apply("p", True)

    groups = kc.realms["p"]["groups"]
    assert [g["name"] for g in groups] == ["users"]
    assert kc.realms["p"]["default_groups"] == [groups[0]["id"]]


def test_each_app_client_gets_the_roles_of_its_own_realm(kc):
    ci.apply("p", True)

    assert kc.roles_of("p", "cnp-p-web") == set(APP_ADMIN_ROLES)
    # The fake rejects a role id from another realm, and `p` stays empty.
    assert kc.roles_of("p-staging", "cnp-p-web-stg") == set(APP_ADMIN_ROLES)
    assert kc.roles_of("p", "cnp-p-web-stg") == set()
    for realm, cid in (("p", "cnp-p-web"), ("p-staging", "cnp-p-web-stg")):
        client = kc.client(realm, cid)
        assert client["serviceAccountsEnabled"] is True
        assert client["webOrigins"] == []
        assert "organization" in client["defaultClientScopes"]
        assert "organization" not in client["optionalClientScopes"]


def test_the_staging_realm_never_opens_sign_up(kc):
    ci.apply("p", True)
    assert kc.realms["p-staging"]["rep"]["registrationAllowed"] is False

    kc.realms["p-staging"]["rep"]["registrationAllowed"] = True
    ci.apply("p", False)
    assert kc.realms["p-staging"]["rep"]["registrationAllowed"] is False


def test_disabling_only_closes_sign_up(kc):
    ci.apply("p", True)
    before = copy.deepcopy(kc.realms)

    ci.apply("p", False)

    before["p"]["rep"]["registrationAllowed"] = False
    assert kc.realms == before


@pytest.mark.parametrize("enabled", [True, False])
def test_applying_twice_changes_nothing(kc, enabled):
    ci.apply("p", enabled)
    kc.writes.clear()

    ci.apply("p", enabled)

    assert kc.writes == []


def test_a_project_without_staging_realm_or_clients_is_fine(kc):
    del kc.realms["p-staging"]
    kc.realms["p"]["clients"] = [
        c
        for c in kc.realms["p"]["clients"]
        if not c["clientId"].startswith("cnp-")
    ]

    ci.apply("p", True)

    assert kc.realms["p"]["rep"]["registrationAllowed"] is True
    assert [g["name"] for g in kc.realms["p"]["groups"]] == ["users"]


def test_other_projects_clients_are_left_alone(kc):
    other = copy.deepcopy(kc.client("p", "cnp-other-x"))
    account = copy.deepcopy(kc.client("p", "account"))

    ci.apply("p", True)

    assert kc.client("p", "cnp-other-x") == other
    assert kc.client("p", "account") == account
    assert kc.roles_of("p", "cnp-other-x") == set()


def test_admin_raises_on_http_errors(monkeypatch):
    monkeypatch.setattr(ci, "_token", lambda: "t")
    response = mock.Mock(status_code=403, text="forbidden")
    with mock.patch.object(ci.requests, "request", return_value=response):
        with pytest.raises(CustomerIdentityError) as err:
            ci._admin("PUT", "/admin/realms/p", {})
    assert err.value.status == 403
