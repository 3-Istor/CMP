"""
Customer sign-up and Organizations in a project's Keycloak realms.

A project owns the realm ``<project>`` (its customers, plus the devs brokered
from the platform realm) and, once staging is on, ``<project>-staging``. When
sign-up is on, each app (client ``cnp-<project>-<app>[-stg]``) manages the
Organizations of its own realm with its own client credentials and the
narrowest roles of that realm's ``realm-management`` client, never
``manage-realm``. Turning sign-up off only closes registration.
"""

import time
from typing import Any

import requests

from app.core.config import settings

# Org CRUD, adding members by user, and looking users up. Not manage-realm.
APP_ADMIN_ROLES = ("manage-organizations", "manage-users", "view-users")

_USERS_GROUP = "users"
_SCOPE = "organization"

_token_cache: tuple[str, float] | None = None


class CustomerIdentityError(RuntimeError):
    """A Keycloak admin call failed; ``status`` is the HTTP status code."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def _token() -> str:
    global _token_cache
    now = time.monotonic()
    if _token_cache and now < _token_cache[1]:
        return _token_cache[0]
    response = requests.post(
        f"{settings.KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": settings.KEYCLOAK_ADMIN_USERNAME,
            "password": settings.KEYCLOAK_ADMIN_PASSWORD,
        },
        timeout=10,
    )
    if response.status_code >= 400:
        raise CustomerIdentityError(
            response.status_code, "Keycloak admin login failed"
        )
    data = response.json()
    ttl = max(data.get("expires_in", 60) - 10, 0)
    _token_cache = (data["access_token"], now + ttl)
    return data["access_token"]


def _admin(method: str, path: str, json: Any | None = None) -> Any:
    response = requests.request(
        method,
        f"{settings.KEYCLOAK_URL}{path}",
        json=json,
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=10,
    )
    if response.status_code >= 400:
        raise CustomerIdentityError(
            response.status_code,
            f"{method} {path}: {response.status_code} {response.text[:200]}",
        )
    return response.json() if response.content else None


def _realm(name: str) -> dict | None:
    try:
        return _admin("GET", f"/admin/realms/{name}")
    except CustomerIdentityError as exc:
        if exc.status == 404:
            return None
        raise


def _set_realm(name: str, current: dict, wanted: dict) -> None:
    changes = {k: v for k, v in wanted.items() if current.get(k) != v}
    if changes:
        _admin("PUT", f"/admin/realms/{name}", changes)


def _default_users_group(realm: str) -> None:
    def find() -> dict | None:
        groups = _admin("GET", f"/admin/realms/{realm}/groups")
        return next((g for g in groups if g["name"] == _USERS_GROUP), None)

    group = find()
    if group is None:
        _admin("POST", f"/admin/realms/{realm}/groups", {"name": _USERS_GROUP})
        group = find()
    defaults = _admin("GET", f"/admin/realms/{realm}/default-groups")
    if group["id"] not in {g["id"] for g in defaults}:
        _admin("PUT", f"/admin/realms/{realm}/default-groups/{group['id']}")


def _grant_apps(realm: str, project: str) -> None:
    base = f"/admin/realms/{realm}"
    clients = _admin("GET", f"{base}/clients")
    apps = [c for c in clients if c["clientId"].startswith(f"cnp-{project}-")]
    if not apps:
        return
    rm = next(c for c in clients if c["clientId"] == "realm-management")
    roles = {
        r["name"]: r for r in _admin("GET", f"{base}/clients/{rm['id']}/roles")
    }
    scopes = {
        s["name"]: s["id"] for s in _admin("GET", f"{base}/client-scopes")
    }
    if not set(APP_ADMIN_ROLES) <= roles.keys() or _SCOPE not in scopes:
        raise CustomerIdentityError(404, f"{realm}: roles or scope missing")

    for app in apps:
        path = f"{base}/clients/{app['id']}"
        if not app.get("serviceAccountsEnabled"):
            # Echo webOrigins: a PUT without them derives them from the
            # redirect URIs.
            _admin(
                "PUT",
                path,
                {
                    "serviceAccountsEnabled": True,
                    "webOrigins": app.get("webOrigins", []),
                },
            )
        user = _admin("GET", f"{path}/service-account-user")
        mapping = f"{base}/users/{user['id']}/role-mappings/clients/{rm['id']}"
        have = {r["name"] for r in _admin("GET", mapping)}
        missing = [roles[n] for n in APP_ADMIN_ROLES if n not in have]
        if missing:
            _admin("POST", mapping, missing)
        if _SCOPE not in app.get("defaultClientScopes", []):
            # Keycloak registers it as optional, and PUT default leaves an
            # optional scope where it is.
            if _SCOPE in app.get("optionalClientScopes", []):
                _admin(
                    "DELETE", f"{path}/optional-client-scopes/{scopes[_SCOPE]}"
                )
            _admin("PUT", f"{path}/default-client-scopes/{scopes[_SCOPE]}")


def apply(project: str, enabled: bool) -> None:
    """
    Make the project's realms match the sign-up switch. Idempotent: a second
    call with the same value writes nothing. Turning it off only closes
    registration; organizations, group and service accounts stay.
    """
    staging = f"{project}-staging"
    realms = {project: _realm(project), staging: _realm(staging)}
    if realms[project] is None:
        raise CustomerIdentityError(404, f"Keycloak realm {project} not found")

    on = {"organizationsEnabled": True} if enabled else {}
    _set_realm(
        project, realms[project], {"registrationAllowed": enabled, **on}
    )
    if realms[staging] is not None:
        _set_realm(
            staging, realms[staging], {"registrationAllowed": False, **on}
        )
    if not enabled:
        return

    _default_users_group(project)
    for name, realm in realms.items():
        if realm is not None:
            _grant_apps(name, project)
