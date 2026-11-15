"""
Membership of the elevated Keycloak groups, and nothing else.

The identity is the ``cmp-provisioner`` client when it is configured
(KEYCLOAK_PROVISIONER_CLIENT_ID/SECRET), which is meant to hold
manage-membership on the elevated groups only. Until then it falls back to
the realm service account the CMP already uses for project groups, which
holds manage-users on the whole realm. Either way, this module refuses any
group outside the catalogue.
"""

import time

import requests

from app.core.config import settings
from app.services import keycloak_admin, keycloak_service
from app.services.access import catalog

_token_cache: tuple[str, float] | None = None


class GroupError(RuntimeError):
    """A Keycloak call failed or the target does not exist."""


def _token() -> str:
    global _token_cache  # pylint: disable=global-statement
    if not keycloak_admin.is_configured():
        # pylint: disable-next=protected-access
        return keycloak_service._get_admin_token()
    now = time.monotonic()
    if _token_cache and now < _token_cache[1]:
        return _token_cache[0]
    response = requests.post(
        keycloak_admin.master_token_url(),
        data=keycloak_admin.master_token_form(),
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    _token_cache = (
        data["access_token"],
        now + max(data.get("expires_in", 60) - 30, 0),
    )
    return data["access_token"]


def _realm_url() -> str:
    return f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}"


def _require_managed(group: str) -> None:
    if group not in catalog.managed_groups():
        raise GroupError(f"'{group}' is not an elevated group.")


def _group_id(group: str, token: str) -> str:
    # pylint: disable-next=protected-access
    found = keycloak_service._find_group_by_name(group, token)
    if not found:
        raise GroupError(f"Keycloak group '{group}' does not exist.")
    return found["id"]


def _user_id(username: str, token: str) -> str:
    # pylint: disable-next=protected-access
    user = keycloak_service._find_user_by_username(username, token)
    if not user:
        raise GroupError(f"Keycloak user '{username}' does not exist.")
    return user["id"]


def _membership(method: str, username: str, group: str) -> None:
    _require_managed(group)
    try:
        token = _token()
        group_id = _group_id(group, token)
        user_id = _user_id(username, token)
        response = requests.request(
            method,
            f"{_realm_url()}/users/{user_id}/groups/{group_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        # Removing someone who is not a member is a 404 on some versions:
        # the goal state is reached all the same.
        if method == "DELETE" and response.status_code == 404:
            return
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GroupError(f"Keycloak {method} on '{group}': {exc}") from exc


def add_member(username: str, group: str) -> None:
    _membership("PUT", username, group)


def remove_member(username: str, group: str) -> None:
    _membership("DELETE", username, group)


def members(group: str) -> set[str]:
    """Usernames in *group*."""
    _require_managed(group)
    try:
        token = _token()
        group_id = _group_id(group, token)
        response = requests.get(
            f"{_realm_url()}/groups/{group_id}/members",
            headers={"Authorization": f"Bearer {token}"},
            params={"max": 500, "briefRepresentation": "true"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GroupError(f"Keycloak members of '{group}': {exc}") from exc
    return {u["username"] for u in response.json() if u.get("username")}
