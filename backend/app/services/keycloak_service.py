"""
Keycloak Service

Provides utilities for:
- Extracting project membership from JWT group claims
- Fetching user groups from the Keycloak Admin API
- Verifying project access for FastAPI dependency injection

Group naming convention: project-<project_name>-admins | project-<project_name>-members
Example: project-sandbox-admins, project-sandbox-members

Performance notes:
- Admin token is cached for 55 seconds (tokens live 60s by default)
- Group IDs are cached for 5 minutes (groups rarely change)
- Member lists are fetched in parallel (admins + members simultaneously)
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

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=True)

# Regex that matches Keycloak project group names
_PROJECT_GROUP_RE = re.compile(
    r"^/?project-(?P<name>[a-z0-9-]+)-(admins|members)$"
)

# ── Simple in-process caches ──────────────────────────────────────────────────

# Admin token cache: (token, expires_at)
_admin_token_cache: tuple[str, float] | None = None

# Group ID cache: {group_name: (group_dict, expires_at)}
_group_cache: dict[str, tuple[dict, float]] = {}
_GROUP_CACHE_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def extract_projects_from_jwt(token_payload: dict) -> list[str]:
    """
    Parse the Keycloak JWT payload and return the list of project names
    the user belongs to (de-duplicated, sorted).

    Groups are expected under the ``groups`` claim as paths like:
      - ``/project-sandbox-admins``
      - ``project-sandbox-members``

    Args:
        token_payload: Decoded JWT payload dict.

    Returns:
        Sorted, unique list of project names (e.g. ``["sandbox", "myteam"]``).
    """
    raw_groups: list[str] = token_payload.get("groups", [])
    projects: set[str] = set()

    for group in raw_groups:
        m = _PROJECT_GROUP_RE.match(group.strip())
        if m:
            projects.add(m.group("name"))

    return sorted(projects)


def has_project_access(token_payload: dict, project_name: str) -> bool:
    """
    Return True if the JWT grants access (admin or member) to *project_name*.

    Args:
        token_payload: Decoded JWT payload dict.
        project_name: Lowercase project identifier (e.g. ``"sandbox"``).

    Returns:
        bool
    """
    raw_groups: list[str] = token_payload.get("groups", [])
    target_admins = f"project-{project_name}-admins"
    target_members = f"project-{project_name}-members"

    for group in raw_groups:
        clean = group.strip().lstrip("/")
        if clean in (target_admins, target_members):
            return True

    return False


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

    url = f"{settings.KEYCLOAK_URL}/realms/3istor/protocol/openid-connect/token"
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
    Results cached for 5 minutes — group IDs rarely change.
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
    _group_cache[group_name] = (group, now + _GROUP_CACHE_TTL)
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


def fetch_user_projects_from_keycloak(user_id: str) -> list[dict]:
    """
    Query the Keycloak Admin API to retrieve the group membership of a user
    and convert it to a list of project dicts.

    Args:
        user_id: Keycloak user UUID (the ``sub`` claim).

    Returns:
        List of project dicts:
        ``[{"name": "sandbox", "role": "admin"}, ...]``
    """
    logger.info(f"🔍 Fetching groups from Keycloak for user_id: {user_id}")

    try:
        admin_token = _get_admin_token()
        url = (
            f"{settings.KEYCLOAK_URL}/admin/realms/3istor"
            f"/users/{user_id}/groups"
        )
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        response.raise_for_status()
        groups: list[dict] = response.json()

        logger.info(f"📋 Keycloak returned {len(groups)} groups for user {user_id}")
        logger.debug(f"Raw groups: {[g.get('name') for g in groups]}")

    except requests.RequestException as exc:
        logger.warning("Could not fetch groups from Keycloak: %s", exc)
        return []

    projects: dict[str, str] = {}  # name -> role (admin wins over member)
    for group in groups:
        group_name: str = group.get("name", "")
        m = _PROJECT_GROUP_RE.match(group_name.strip())
        if m:
            project_name = m.group("name")
            role_str = m.group(2) if len(m.groups()) >= 2 else ""
            # Resolve via the full match to get the suffix
            suffix = group_name.rsplit("-", 1)[-1]  # "admins" or "members"
            role = "admin" if suffix == "admins" else "member"
            # Admin role wins over member role for the same project
            if project_name not in projects or role == "admin":
                projects[project_name] = role
                logger.debug(f"  ✅ Matched project group: {group_name} → project={project_name}, role={role}")

    logger.info(f"📊 Extracted {len(projects)} projects: {list(projects.keys())}")

    return [{"name": name, "role": role} for name, role in sorted(projects.items())]


def add_user_to_project(username: str, project_name: str, role: str) -> None:
    """
    Add a user to a project Keycloak group.

    Args:
        username: Keycloak username.
        project_name: Project identifier (e.g. ``"sandbox"``).
        role: Either ``"admin"`` or ``"member"``.

    Raises:
        ValueError: If user or group not found, or role is invalid.
        requests.RequestException: On Keycloak API errors.
    """
    if role not in ("admin", "member"):
        raise ValueError(f"Invalid role '{role}'. Must be 'admin' or 'member'.")

    admin_token = _get_admin_token()

    # Find user
    user = _find_user_by_username(username, admin_token)
    if not user:
        raise ValueError(f"User '{username}' not found in Keycloak.")

    user_id = user["id"]

    # Construct group name
    group_suffix = "admins" if role == "admin" else "members"
    group_name = f"project-{project_name}-{group_suffix}"

    # Find group
    group = _find_group_by_name(group_name, admin_token)
    if not group:
        raise ValueError(
            f"Group '{group_name}' not found. "
            f"Ensure the project '{project_name}' has been bootstrapped."
        )

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
    Remove a user from BOTH admin and member groups of a project.

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

    # Remove from both groups (if they exist)
    for suffix in ("admins", "members"):
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


def list_project_members(project_name: str) -> list[dict]:
    """
    List all members of a project (both admins and members).

    Optimised: the two group lookups and their member fetches run in parallel.

    Returns:
        Sorted list of member dicts with role resolved (admin wins over member).
    """
    admin_token = _get_admin_token()

    def _fetch_group_members(suffix: str, role: str) -> list[dict]:
        """Fetch members of one group, returning [] if the group doesn't exist."""
        group_name = f"project-{project_name}-{suffix}"
        group = _find_group_by_name(group_name, admin_token)
        if not group:
            logger.debug("Group '%s' not found — skipping", group_name)
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
                "role": role,
            }
            for u in response.json()
            if u.get("username")
        ]

    # ── Fetch admins and members in parallel ─────────────────────────────
    members: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(_fetch_group_members, "admins", "admin"): "admin",
            pool.submit(_fetch_group_members, "members", "member"): "member",
        }
        for future in as_completed(futures):
            role = futures[future]
            try:
                for user in future.result():
                    username = user["username"]
                    # Admin role wins over member role for the same user
                    if username not in members or role == "admin":
                        members[username] = user
            except Exception as exc:
                logger.warning("Failed to fetch %s group: %s", role, exc)

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
            options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
        )

        # DEBUG: Log what's in the token
        logger.debug(f"🔑 JWT payload keys: {list(payload.keys())}")
        logger.debug(f"🔑 sub={payload.get('sub', 'MISSING')}, preferred_username={payload.get('preferred_username', 'MISSING')}")

        return payload
    except jwt.DecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc


def verify_project_access(project_name: str, require_admin: bool = False):
    """
    FastAPI dependency factory for real-time RBAC verification.

    Unlike JWT-only checks, this queries the Keycloak Admin API to verify
    current group membership, preventing token staleness issues.

    Args:
        project_name: Project identifier to check access for.
        require_admin: If True, requires admin role. If False, allows admin or member.

    Returns:
        FastAPI dependency that raises HTTPException 403 on access denial.

    Usage::

        @router.get("/api/projects/{project_name}/apps")
        async def list_apps(
            project_name: str,
            user: dict = Depends(verify_project_access(project_name)),
        ): ...

        @router.post("/api/projects/{project_name}/members")
        async def add_member(
            project_name: str,
            user: dict = Depends(verify_project_access(project_name, require_admin=True)),
        ): ...
    """

    async def _inner(
        token_payload: Annotated[dict, Depends(get_current_user)],
    ) -> dict:
        user_id = token_payload.get("sub", "")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not determine user identity from token.",
            )

        try:
            admin_token = _get_admin_token()

            # Check admin group
            admin_group_name = f"project-{project_name}-admins"
            admin_group = _find_group_by_name(admin_group_name, admin_token)

            if admin_group:
                is_admin = _check_user_in_group_realtime(
                    user_id, admin_group["id"], admin_token
                )
                if is_admin:
                    return token_payload  # Admin access granted

            # If admin role required but user is not admin, deny
            if require_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: admin role required for project '{project_name}'.",
                )

            # Check member group
            member_group_name = f"project-{project_name}-members"
            member_group = _find_group_by_name(member_group_name, admin_token)

            if member_group:
                is_member = _check_user_in_group_realtime(
                    user_id, member_group["id"], admin_token
                )
                if is_member:
                    return token_payload  # Member access granted

            # No access found
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: you are not a member of project '{project_name}'.",
            )

        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "Error verifying project access for user %s in project %s: %s",
                user_id,
                project_name,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to verify project access. Please try again.",
            ) from exc

    return _inner
