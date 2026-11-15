"""
What the access review reads from, and changes in, Keycloak realm 3istor.

Last login. Keycloak 26 keeps no last-login field on a user. It exposes:

- stored events (``GET /events?type=LOGIN``), with the user id and time of
  every login to any client, SSO cookie logins included, kept for the
  realm's ``eventsExpiration`` (30 days in the K3s Terraform);
- the live sessions of a user (``GET /users/{id}/sessions``), whose
  ``lastAccess`` moves with every token refresh but vanishes at logout or
  expiry;
- ``createdTimestamp`` on the user.

The CMP reads the LOGIN events daily and keeps the latest per user in
``account_activity``, so the answer outlives the 30 days. A live session
counts as a login (someone refreshing tokens is not inactive). With no login
seen, the account is dated from its creation, or from the oldest moment the
event store covered when the CMP first saw the user, whichever is later.

The identity is the realm service account the CMP already uses for project
groups. Reading events needs its ``view-events`` realm-management role.
"""

from datetime import datetime, timedelta, timezone

import requests

from app.core.config import settings
from app.services import keycloak_service

PAGE = 200
# Used when the realm keeps events forever: no need to read further back.
LOOKBACK_CAP = timedelta(days=90)


class DirectoryError(RuntimeError):
    """Keycloak could not answer, or does not record logins."""


def _get(path: str, params: dict | None = None) -> object:
    try:
        # pylint: disable-next=protected-access
        token = keycloak_service._get_admin_token()
        response = requests.get(
            f"{_realm_url()}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DirectoryError(f"Keycloak GET {path}: {exc}") from exc
    return response.json()


def _realm_url() -> str:
    return f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}"


def from_millis(value: int | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).replace(
        tzinfo=None
    )


def event_retention() -> timedelta | None:
    """
    How far back the event store reaches, None when it keeps everything.
    Raises DirectoryError when login events are not stored at all.
    """
    config = _get("/events/config")
    if not config.get("eventsEnabled"):
        raise DirectoryError("Login events are not stored in this realm.")
    types = config.get("enabledEventTypes") or []
    if types and "LOGIN" not in types:
        raise DirectoryError("LOGIN is not among the stored event types.")
    seconds = config.get("eventsExpiration")
    return timedelta(seconds=seconds) if seconds else None


def login_events(since: datetime) -> dict[str, datetime]:
    """The latest LOGIN time per user id since *since*."""
    latest: dict[str, datetime] = {}
    first = 0
    while True:
        page = _get(
            "/events",
            {
                "type": "LOGIN",
                "dateFrom": since.date().isoformat(),
                "first": first,
                "max": PAGE,
            },
        )
        for event in page:
            when = from_millis(event.get("time"))
            user_id = event.get("userId")
            if when and user_id and when > latest.get(user_id, when.min):
                latest[user_id] = when
        if len(page) < PAGE:
            return latest
        first += PAGE


def users() -> list[dict]:
    """Every realm user: id, username, enabled, createdTimestamp."""
    out: list[dict] = []
    first = 0
    while True:
        page = _get("/users", {"first": first, "max": PAGE})
        out.extend(page)
        if len(page) < PAGE:
            return out
        first += PAGE


def last_session_access(user_id: str) -> datetime | None:
    sessions = _get(f"/users/{user_id}/sessions")
    times = [
        from_millis(s.get("lastAccess") or s.get("start")) for s in sessions
    ]
    return max((t for t in times if t), default=None)


def group_members(group: str) -> list[str]:
    """Usernames of a top-level group; empty when it does not exist."""
    try:
        # pylint: disable-next=protected-access
        token = keycloak_service._get_admin_token()
        # pylint: disable-next=protected-access
        found = keycloak_service._find_group_by_name(group, token)
    except requests.RequestException as exc:
        raise DirectoryError(f"Keycloak group '{group}': {exc}") from exc
    if not found:
        return []
    members = _get(
        f"/groups/{found['id']}/members",
        {"max": 500, "briefRepresentation": "true"},
    )
    return sorted(u["username"] for u in members if u.get("username"))


def set_enabled(user_id: str, enabled: bool) -> None:
    """
    Enable or disable a user. The whole representation is sent back: with
    the declarative user profile of Keycloak 24+, a partial PUT can drop
    the attributes it leaves out.
    """
    user = _get(f"/users/{user_id}")
    user["enabled"] = enabled
    try:
        # pylint: disable-next=protected-access
        token = keycloak_service._get_admin_token()
        response = requests.put(
            f"{_realm_url()}/users/{user_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=user,
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DirectoryError(f"Keycloak PUT user {user_id}: {exc}") from exc
