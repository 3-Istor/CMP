"""
Customer sign-up and Organizations in a project's Keycloak realms.

A project owns the realm ``<project>`` (its customers, plus the devs brokered
from the platform realm) and, once staging is on, ``<project>-staging``. When
sign-up is on, each app (client ``cnp-<project>-<app>[-stg]``) manages the
Organizations of its own realm with its own client credentials and the
narrowest roles of that realm's ``realm-management`` client, never
``manage-realm``. Turning sign-up off only closes registration.
"""

import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

import requests

from app.core.config import settings
from app.services import keycloak_admin, project_registry

logger = logging.getLogger(__name__)

# Org CRUD and members by user; manage-users already implies view-users.
# Not manage-realm.
APP_ADMIN_ROLES = ("manage-organizations", "manage-users")

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
        keycloak_admin.master_token_url(),
        data=keycloak_admin.master_token_form(),
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
    try:
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
        # Also catches a non-JSON 2xx (JSONDecodeError is a RequestException).
        return response.json() if response.content else None
    except requests.RequestException as exc:
        raise CustomerIdentityError(502, f"{method} {path}: {exc}") from exc


def _base(realm: str) -> str:
    return f"/admin/realms/{quote(realm, safe='')}"


def _realm(name: str) -> dict | None:
    try:
        return _admin("GET", _base(name))
    except CustomerIdentityError as exc:
        if exc.status == 404:
            return None
        raise


def _set_realm(name: str, current: dict, wanted: dict) -> None:
    changes = {k: v for k, v in wanted.items() if current.get(k) != v}
    if changes:
        _admin("PUT", _base(name), changes)


def _default_users_group(realm: str) -> None:
    base = _base(realm)

    def find() -> dict | None:
        # By name: the plain listing is paged, and the group may fall off it.
        groups = _admin(
            "GET", f"{base}/groups?search={_USERS_GROUP}&exact=true"
        )
        return next((g for g in groups if g["name"] == _USERS_GROUP), None)

    group = find()
    if group is None:
        _admin("POST", f"{base}/groups", {"name": _USERS_GROUP})
        group = find()
    defaults = _admin("GET", f"{base}/default-groups")
    if group["id"] not in {g["id"] for g in defaults}:
        _admin("PUT", f"{base}/default-groups/{group['id']}")


def _grant_apps(realm: str, project: str, staging: bool) -> None:
    base = _base(realm)
    clients = _admin("GET", f"{base}/clients")
    # `-stg` clients only get rights in the staging realm, never in prod's.
    apps = [
        c
        for c in clients
        if c["clientId"].startswith(f"cnp-{project}-")
        and c["clientId"].endswith("-stg") == staging
    ]
    if not apps:
        return
    rm = next(
        (c for c in clients if c["clientId"] == "realm-management"), None
    )
    if rm is None:
        raise CustomerIdentityError(
            502, f"{realm}: no realm-management client"
        )
    roles = {
        r["name"]: r for r in _admin("GET", f"{base}/clients/{rm['id']}/roles")
    }
    scopes = {
        s["name"]: s["id"] for s in _admin("GET", f"{base}/client-scopes")
    }
    if not set(APP_ADMIN_ROLES) <= roles.keys() or _SCOPE not in scopes:
        raise CustomerIdentityError(502, f"{realm}: roles or scope missing")

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
    live = {name: rep for name, rep in realms.items() if rep is not None}

    # Staging never takes sign-ups, and closing is always safe. Opening the
    # project realm waits until the apps and the default group are ready.
    for name, rep in live.items():
        wanted = {"organizationsEnabled": True} if enabled else {}
        if name != project or not enabled:
            wanted["registrationAllowed"] = False
        _set_realm(name, rep, wanted)
    if not enabled:
        return

    _default_users_group(project)
    for name in live:
        _grant_apps(name, project, staging=name != project)
    _set_realm(project, realms[project], {"registrationAllowed": True})


def apply_if_enabled(project: str) -> None:
    """
    Re-apply the switch when the project has it on, so a new app client gets
    its roles. Never raises: a Keycloak or registry failure must not fail the
    deployment. Blocking and starts its own event loop: from async code, call
    it through ``asyncio.to_thread``.
    """
    try:
        if asyncio.run(project_registry.is_self_registration_on(project)):
            apply(project, True)
    except Exception:
        logger.exception("Customer sign-up not applied to '%s'", project)
