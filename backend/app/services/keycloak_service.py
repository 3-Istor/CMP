"""
Keycloak Service

Provides utilities for:
- Extracting project membership from JWT group claims
- Fetching user groups from the Keycloak Admin API
- Verifying project access for FastAPI dependency injection

Group naming convention: project-<project_name>-admins | project-<project_name>-members
Example: project-sandbox-admins, project-sandbox-members
"""

import logging
import re
from typing import Annotated

import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=True)

# Regex that matches Keycloak project group names
_PROJECT_GROUP_RE = re.compile(
    r"^/?project-(?P<name>[a-z0-9-]+)-(admins|members)$"
)


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
    """Obtain a Keycloak admin token via client-credentials grant."""
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
    return response.json()["access_token"]


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

    return [{"name": name, "role": role} for name, role in sorted(projects.items())]


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
            options={"verify_signature": False, "verify_aud": False},
        )
        return payload
    except jwt.DecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc


def verify_project_access(project_name: str):
    """
    FastAPI dependency factory.

    Usage::

        @router.get("/api/projects/{project_name}/apps")
        async def list_apps(
            project_name: str,
            _: None = Depends(verify_project_access(project_name)),
        ): ...

    Because FastAPI path parameters are not available at dependency-factory
    call time, the preferred pattern is to inject the dependency inside the
    route handler using ``Depends`` with a closure, or to use the inline
    approach shown in the projects router.
    """

    async def _inner(
        token_payload: Annotated[dict, Depends(get_current_user)],
    ) -> dict:
        if not has_project_access(token_payload, project_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Access denied: you are not a member of project '{project_name}'."
                ),
            )
        return token_payload

    return _inner
