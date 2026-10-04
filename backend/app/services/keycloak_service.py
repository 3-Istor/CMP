"""
Keycloak Service

Provides utilities for:
- Resolving a user's role on a project from the Keycloak Admin API
- Managing project group membership (add / remove / list)
- Decoding the Bearer JWT for FastAPI dependency injection

Group naming convention: project-<project_name>-<admins|members|guests>
Example: project-sandbox-admins, project-sandbox-guests

The ``guests`` group is created by the ``project-bootstrap`` Terraform
module alongside the other two. Projects bootstrapped before guests existed
have no such group: every read path here degrades to "nobody is a guest",
and only the write path complains — loudly, via
:class:`ProjectGroupMissingError`.

Note that group membership is always resolved against the Admin API, never
from the JWT's ``groups`` claim, which is not populated in this realm.

Performance notes:
- Admin token is cached for 55 seconds (tokens live 60s by default)
- Group IDs are cached for 5 minutes (groups rarely change); misses are not
  cached, so a freshly created group is visible immediately
- Member lists are fetched in parallel (one request per group)
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Annotated

import jwt
import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.roles import (
    ALL_GROUP_SUFFIXES,
    ASSIGNABLE,
    GROUP_SUFFIX,
    SUFFIX_TO_ROLE,
    ProjectRole,
    rank,
)

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=True)

# Regex that matches Keycloak project group names. The suffix is captured so
# the role can be looked up directly instead of re-parsing the group name.
_PROJECT_GROUP_RE = re.compile(
    r"^/?project-(?P<name>[a-z0-9-]+)-(?P<suffix>admins|members|guests)$"
)


class ProjectGroupMissingError(ValueError):
    """
    A project group that should exist does not.

    In practice this means the project was bootstrapped before the guests
    group was added to the Terraform module. Carries the group name so
    callers can name it in the response.
    """

    def __init__(self, group_name: str) -> None:
        self.group_name = group_name
        super().__init__(f"Keycloak group '{group_name}' does not exist.")

# ── Simple in-process caches ──────────────────────────────────────────────────

# Admin token cache: (token, expires_at)
_admin_token_cache: tuple[str, float] | None = None

# Group ID cache: {group_name: (group_dict, expires_at)}
_group_cache: dict[str, tuple[dict, float]] = {}
_GROUP_CACHE_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def get_user_id_from_token(token_payload: dict) -> str:
    """
    Extract the Keycloak user UUID from a decoded token.

    Falls back to a username lookup when the ``sub`` claim is missing.

    Args:
        token_payload: Decoded JWT token payload.

    Returns:
        str: User ID (UUID).

    Raises:
        HTTPException: If the user cannot be determined.
    """
    user_id = token_payload.get("sub", "")
    username = token_payload.get("preferred_username", "")

    # If sub is empty but we have username, lookup user_id from Keycloak
    if not user_id and username:
        logger.warning(
            "⚠️  Token missing 'sub' claim, looking up user_id from "
            "username '%s'",
            username,
        )
        try:
            admin_token = _get_admin_token()
            user = _find_user_by_username(username, admin_token)
            if user:
                user_id = user["id"]
                logger.info(
                    "✅ Found user_id '%s' for username '%s'",
                    user_id,
                    username,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User '{username}' not found in Keycloak",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("❌ Failed to lookup user: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to lookup user: {exc}",
            ) from exc

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not determine user identity from token.",
        )

    return user_id


# ---------------------------------------------------------------------------
# Keycloak Admin API helpers
# ---------------------------------------------------------------------------


def _get_admin_token() -> str:
    """
    Obtain a Keycloak admin token via client-credentials grant.
    Cached for 55 seconds to avoid a round-trip on every request.
    """
    global _admin_token_cache

    now = time.monotonic()
    if _admin_token_cache is not None:
        token, expires_at = _admin_token_cache
        if now < expires_at:
            return token

    url = (
        f"{settings.KEYCLOAK_URL}/realms/3istor/protocol/openid-connect/token"
    )
    response = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.KEYCLOAK_CLIENT_ID,
            "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
        },
        timeout=10,
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    # Cache for 55 s (tokens typically live 60 s)
    _admin_token_cache = (token, now + 55)
    logger.debug("🔑 Fetched fresh Keycloak admin token (cached 55s)")
    return token


def _find_user_by_username(username: str, admin_token: str) -> dict | None:
    """
    Search for a Keycloak user by username.

    Args:
        username: Keycloak username (or email if configured).
        admin_token: Admin API bearer token.

    Returns:
        User dict if found, None otherwise.
    """
    url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"username": username, "exact": "true"},
        timeout=10,
    )
    response.raise_for_status()
    users = response.json()
    return users[0] if users else None


def _find_group_by_name(group_name: str, admin_token: str) -> dict | None:
    """
    Search for a Keycloak group by exact name.

    Hits are cached for 5 minutes — group IDs rarely change. Misses are NOT
    cached: a project's guests group may be created by Terraform at any
    moment, and caching the "not found" would keep rejecting guest
    assignments for minutes after the operator fixed the project.
    """
    now = time.monotonic()
    cached = _group_cache.get(group_name)
    if cached is not None:
        group, expires_at = cached
        if now < expires_at:
            return group

    url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/groups"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"search": group_name, "exact": "true"},
        timeout=10,
    )
    response.raise_for_status()
    groups = response.json()
    # Keycloak search returns partial matches — filter exact
    group = next((g for g in groups if g.get("name") == group_name), None)
    if group is not None:
        _group_cache[group_name] = (group, now + _GROUP_CACHE_TTL)
    else:
        # Drop any stale entry so a later hit isn't shadowed.
        _group_cache.pop(group_name, None)
    return group


def _check_user_in_group_realtime(
    user_id: str, group_id: str, admin_token: str
) -> bool:
    """
    Query the Keycloak Admin API to check if a user is currently in a group.

    Args:
        user_id: User UUID (sub claim).
        group_id: Group UUID.
        admin_token: Admin API bearer token.

    Returns:
        True if user is in the group, False otherwise.
    """
    url = f"{settings.KEYCLOAK_URL}/admin/realms/3istor/users/{user_id}/groups"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        response.raise_for_status()
        user_groups = response.json()
        return any(g.get("id") == group_id for g in user_groups)
    except requests.RequestException as exc:
        logger.warning(
            "Failed to check group membership for user %s: %s", user_id, exc
        )
        return False


def _fetch_user_groups(user_id: str) -> list[dict]:
    """
    Return every Keycloak group a user belongs to.

    One Admin API call. ``briefRepresentation`` trims the payload to what the
    callers need (the name), and ``max`` lifts the default page size, which
    would otherwise silently truncate the groups of a user who belongs to
    many projects.

    Returns an empty list — never raises — when Keycloak is unreachable, so a
    transient outage reads as "no projects" rather than a 500. Callers that
    must distinguish the two should probe Keycloak themselves.
    """
    try:
        admin_token = _get_admin_token()
        url = (
            f"{settings.KEYCLOAK_URL}/admin/realms/3istor"
            f"/users/{user_id}/groups"
        )
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"briefRepresentation": "true", "max": 500},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("Could not fetch groups from Keycloak: %s", exc)
        return []


def _project_roles_from_groups(groups: list[dict]) -> dict[str, ProjectRole]:
    """
    Map Keycloak groups to ``{project_name: role}``.

    A user can sit in several groups of one project — being added as admin
    never removed them from ``-members``. The strongest role wins.
    """
    projects: dict[str, ProjectRole] = {}

    for group in groups:
        group_name: str = group.get("name", "")
        match = _PROJECT_GROUP_RE.match(group_name.strip())
        if not match:
            continue

        project_name = match.group("name")
        role = SUFFIX_TO_ROLE[match.group("suffix")]

        if rank(role) > rank(projects.get(project_name)):
            projects[project_name] = role

    return projects


def fetch_user_projects_from_keycloak(user_id: str) -> list[dict]:
    """
    Query the Keycloak Admin API to retrieve the group membership of a user
    and convert it to a list of project dicts.

    Args:
        user_id: Keycloak user UUID (the ``sub`` claim).

    Returns:
        List of project dicts, sorted by name:
        ``[{"name": "sandbox", "role": "admin"}, ...]``
    """
    logger.info("🔍 Fetching groups from Keycloak for user_id: %s", user_id)

    groups = _fetch_user_groups(user_id)
    logger.debug("Raw groups: %s", [g.get("name") for g in groups])

    projects = _project_roles_from_groups(groups)
    logger.info(
        "📊 Extracted %d projects: %s", len(projects), list(projects)
    )

    return [
        {"name": name, "role": role.value}
        for name, role in sorted(projects.items())
    ]


def get_user_project_role(
    user_id: str, project_name: str
) -> ProjectRole | None:
    """
    Return the user's role on one project, or None if they have none.

    This is the primitive every authorization check is built on. It costs a
    single Admin API call — the user's own group list — and matches locally.
    The alternative (looking up each project group's id, then probing
    membership) costs up to six calls and grows with every new role.

    ``owner`` is never returned: it lives in the database, and the caller
    that has a session resolves it. See ``app.services.authz``.
    """
    groups = _fetch_user_groups(user_id)
    return _project_roles_from_groups(groups).get(project_name)


def add_user_to_project(username: str, project_name: str, role: str) -> None:
    """
    Add a user to a project Keycloak group.

    Does not clear the user's other groups on the project — callers changing
    someone's role must call :func:`remove_user_from_project` first, or the
    old group lingers and the stronger role keeps winning.

    Args:
        username: Keycloak username.
        project_name: Project identifier (e.g. ``"sandbox"``).
        role: One of ``"admin"``, ``"member"`` or ``"guest"``.

    Raises:
        ValueError: If the role is invalid or the user is not found.
        ProjectGroupMissingError: If the target group does not exist.
        requests.RequestException: On Keycloak API errors.
    """
    try:
        target_role = ProjectRole(role)
    except ValueError as exc:
        raise ValueError(f"Invalid role '{role}'.") from exc

    if target_role not in ASSIGNABLE:
        allowed = ", ".join(repr(r.value) for r in ASSIGNABLE)
        raise ValueError(
            f"Invalid role '{role}'. Must be one of: {allowed}."
        )

    admin_token = _get_admin_token()

    # Find user
    user = _find_user_by_username(username, admin_token)
    if not user:
        raise ValueError(f"User '{username}' not found in Keycloak.")

    user_id = user["id"]

    # Construct group name
    group_name = f"project-{project_name}-{GROUP_SUFFIX[target_role]}"

    # Find group
    group = _find_group_by_name(group_name, admin_token)
    if not group:
        raise ProjectGroupMissingError(group_name)

    group_id = group["id"]

    # Add user to group
    url = (
        f"{settings.KEYCLOAK_URL}/admin/realms/3istor"
        f"/users/{user_id}/groups/{group_id}"
    )
    response = requests.put(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    response.raise_for_status()

    logger.info(
        "Added user '%s' to group '%s' (role: %s)", username, group_name, role
    )


def remove_user_from_project(username: str, project_name: str) -> None:
    """
    Remove a user from EVERY role group of a project.

    Used both to drop someone from a project and, before an
    :func:`add_user_to_project`, to change their role: without this the user
    keeps their old group and the stronger of the two keeps winning.

    A group that does not exist is skipped, so this is safe on projects
    bootstrapped before the guests group was introduced.

    Args:
        username: Keycloak username.
        project_name: Project identifier.

    Raises:
        ValueError: If user not found.
        requests.RequestException: On Keycloak API errors.
    """
    admin_token = _get_admin_token()

    # Find user
    user = _find_user_by_username(username, admin_token)
    if not user:
        raise ValueError(f"User '{username}' not found in Keycloak.")

    user_id = user["id"]

    # Remove from every role group (if they exist)
    for suffix in ALL_GROUP_SUFFIXES:
        group_name = f"project-{project_name}-{suffix}"
        group = _find_group_by_name(group_name, admin_token)

        if not group:
            logger.debug("Group '%s' not found — skipping", group_name)
            continue

        group_id = group["id"]

        # Check if user is in this group first
        if not _check_user_in_group_realtime(user_id, group_id, admin_token):
            logger.debug(
                "User '%s' not in group '%s' — skipping", username, group_name
            )
            continue

        # Remove user from group
        url = (
            f"{settings.KEYCLOAK_URL}/admin/realms/3istor"
            f"/users/{user_id}/groups/{group_id}"
        )
        response = requests.delete(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        response.raise_for_status()

        logger.info("Removed user '%s' from group '%s'", username, group_name)


def project_guests_supported(project_name: str) -> bool:
    """
    Return True if this project has a guests group.

    Projects bootstrapped before guest support was added to the
    ``project-bootstrap`` Terraform module do not. The UI uses this to hide
    the Guest option rather than letting an admin pick it and collect a 409.
    """
    admin_token = _get_admin_token()
    group_name = (
        f"project-{project_name}-{GROUP_SUFFIX[ProjectRole.GUEST]}"
    )
    return _find_group_by_name(group_name, admin_token) is not None


def list_project_members(project_name: str) -> list[dict]:
    """
    List every member of a project, across all role groups.

    Optimised: the group lookups and their member fetches run in parallel.

    Returns:
        Sorted list of member dicts with the strongest role resolved per
        user.
    """
    admin_token = _get_admin_token()

    def _fetch_group_members(suffix: str, role: ProjectRole) -> list[dict]:
        """Fetch members of one group, returning [] if the group is absent."""
        group_name = f"project-{project_name}-{suffix}"
        group = _find_group_by_name(group_name, admin_token)
        if not group:
            logger.info(
                "Group '%s' does not exist — treating it as empty. Re-run "
                "the project-bootstrap Terraform module for '%s' to create "
                "it.",
                group_name,
                project_name,
            )
            return []

        url = (
            f"{settings.KEYCLOAK_URL}/admin/realms/3istor"
            f"/groups/{group['id']}/members"
        )
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        response.raise_for_status()
        return [
            {
                "username": u.get("username", ""),
                "email": u.get("email", ""),
                "first_name": u.get("firstName", ""),
                "last_name": u.get("lastName", ""),
                "role": role.value,
            }
            for u in response.json()
            if u.get("username")
        ]

    # ── Fetch every role group in parallel ───────────────────────────────
    members: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(ASSIGNABLE)) as pool:
        futures = {
            pool.submit(
                _fetch_group_members, GROUP_SUFFIX[role], role
            ): role
            for role in ASSIGNABLE
        }
        for future in as_completed(futures):
            role = futures[future]
            try:
                for user in future.result():
                    username = user["username"]
                    # The strongest role wins when a user is in several groups
                    existing = members.get(username)
                    if existing is None or rank(role) > rank(
                        existing["role"]
                    ):
                        members[username] = user
            except Exception as exc:
                logger.warning(
                    "Failed to fetch %s group: %s", role.value, exc
                )

    return sorted(members.values(), key=lambda x: x["username"])


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> dict:
    """
    FastAPI dependency — decodes the Bearer JWT (Envoy already validated it)
    and returns the payload dict.

    Raises HTTPException 401 on malformed tokens.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
            },
        )

        # DEBUG: Log what's in the token
        logger.debug(f"🔑 JWT payload keys: {list(payload.keys())}")
        logger.debug(
            f"🔑 sub={payload.get('sub', 'MISSING')}, preferred_username={payload.get('preferred_username', 'MISSING')}"
        )

        return payload
    except jwt.DecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc
