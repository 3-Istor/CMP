"""
Grafana Service

Provides utilities for programmatically managing user membership in Grafana Organizations.

Since Grafana OSS doesn't support dynamic OIDC group-to-org mapping, the CMP
backend synchronises Keycloak project membership into Grafana via the Admin API.

Organization naming convention: "Project <TitleCasedProjectName>"
  Example: project "alpha" -> "Project Alpha", project "sandbox" -> "Project Sandbox"

Role mappings (CMP to Grafana):
  - "admin" or "owner" -> "Admin"
  - "member" -> "Editor"

All operations are async and use connection pooling via httpx.AsyncClient.
Errors are logged but do not crash the caller (graceful degradation).
"""

import logging
from typing import Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Grafana Admin API credentials
GRAFANA_BASE_URL = "https://grafana.3istor.com"
GRAFANA_ADMIN_USERNAME = "admin"

# Shared async HTTP client (connection pooling)
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """
    Get or create the shared async HTTP client for Grafana API calls.

    Uses connection pooling and 10-second timeouts.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            auth=(GRAFANA_ADMIN_USERNAME, settings.GRAFANA_ADMIN_PASSWORD),
            timeout=10.0,
            follow_redirects=True,
        )
    return _http_client


async def _get_org_id_by_name(org_name: str) -> int | None:
    """
    Get Grafana organization ID by exact name.

    Args:
        org_name: Organization name (e.g. "Project Alpha").

    Returns:
        Organization ID (int) if found, None otherwise.
    """
    client = _get_http_client()
    url = f"{GRAFANA_BASE_URL}/api/orgs/name/{org_name}"

    try:
        response = await client.get(url)
        if response.status_code == 404:
            logger.debug(f"📊 Grafana org '{org_name}' not found")
            return None
        response.raise_for_status()
        data = response.json()
        org_id = data.get("id")
        logger.debug(f"📊 Grafana org '{org_name}' → ID={org_id}")
        return org_id
    except httpx.HTTPStatusError as exc:
        logger.warning(
            f"⚠️  Failed to get Grafana org '{org_name}': HTTP {exc.response.status_code}"
        )
        return None
    except Exception as exc:
        logger.warning(f"⚠️  Failed to get Grafana org '{org_name}': {exc}")
        return None


async def _get_user_id_by_username(username: str) -> int | None:
    """
    Get Grafana user ID by username/login.

    Args:
        username: Keycloak username (matches Grafana login via OIDC SSO).

    Returns:
        User ID (int) if found, None otherwise.
    """
    client = _get_http_client()
    url = f"{GRAFANA_BASE_URL}/api/users/lookup"

    try:
        response = await client.get(url, params={"loginOrEmail": username})
        if response.status_code == 404:
            logger.debug(f"📊 Grafana user '{username}' not found")
            return None
        response.raise_for_status()
        data = response.json()
        user_id = data.get("id")
        logger.debug(f"📊 Grafana user '{username}' → ID={user_id}")
        return user_id
    except httpx.HTTPStatusError as exc:
        logger.warning(
            f"⚠️  Failed to get Grafana user '{username}': HTTP {exc.response.status_code}"
        )
        return None
    except Exception as exc:
        logger.warning(f"⚠️  Failed to get Grafana user '{username}': {exc}")
        return None


def _title_case_project_name(project_name: str) -> str:
    """
    Convert project name to title case for Grafana org name.

    IMPORTANT: Keeps hyphens intact to match Terraform's naming convention.

    Examples:
        "alpha" -> "Alpha"
        "my-team" -> "My-Team"
        "brian-b6" -> "Brian-B6"
        "sandbox" -> "Sandbox"

    Args:
        project_name: Lowercase kebab-case project identifier.

    Returns:
        Title-cased project name with hyphens preserved.
    """
    # Split by hyphen, title-case each part, then rejoin with hyphens
    parts = project_name.split("-")
    return "-".join(part.capitalize() for part in parts)


def _grafana_org_name(project_name: str) -> str:
    """
    Generate Grafana organization name from project name.

    Convention: "Project <TitleCasedProjectName>"

    Args:
        project_name: Lowercase project identifier (e.g. "alpha").

    Returns:
        Grafana org name (e.g. "Project Alpha").
    """
    title = _title_case_project_name(project_name)
    return f"Project {title}"


def _map_role_to_grafana(
    role: str,
) -> Literal["Admin", "Editor", "Viewer"]:
    """
    Map CMP role to Grafana org role.

    Args:
        role: CMP role ("admin", "owner", "member").

    Returns:
        Grafana role ("Admin", "Editor", "Viewer").
    """
    if role.lower() in ("admin", "owner"):
        return "Admin"
    elif role.lower() == "member":
        return "Editor"
    else:
        # Fallback to Viewer for unknown roles
        logger.warning(f"⚠️  Unknown role '{role}' — defaulting to Viewer")
        return "Viewer"


async def add_user_to_project_org(
    project_name: str, username: str, role: str
) -> bool:
    """
    Add a user to a Grafana organization with the specified role.

    If the user already exists in the org, their role is updated.
    If the org or user doesn't exist yet (e.g., during bootstrap or before
    first login), the operation fails gracefully with a warning log.

    IMPORTANT: Fetches user email from Keycloak since Grafana OIDC uses email as login.

    Args:
        project_name: Lowercase project identifier (e.g. "sandbox").
        username: Keycloak username (will be used to fetch email).
        role: CMP role ("admin", "owner", "member").

    Returns:
        True if successful, False otherwise.
    """
    org_name = _grafana_org_name(project_name)
    grafana_role = _map_role_to_grafana(role)

    logger.info(
        f"📊 Adding user '{username}' to Grafana org '{org_name}' with role '{grafana_role}'"
    )

    # Step 1: Get user email from Keycloak (Grafana OIDC uses email as login)
    try:
        from app.services.keycloak_service import (
            _find_user_by_username,
            _get_admin_token,
        )

        admin_token = _get_admin_token()
        keycloak_user = _find_user_by_username(username, admin_token)
        if not keycloak_user or not keycloak_user.get("email"):
            logger.warning(
                f"⚠️  User '{username}' not found in Keycloak or has no email — Grafana sync skipped"
            )
            return False

        user_email = keycloak_user["email"]
        logger.debug(
            f"📊 Resolved username '{username}' → email '{user_email}'"
        )

    except Exception as exc:
        logger.warning(
            f"⚠️  Failed to fetch user '{username}' from Keycloak: {exc}"
        )
        return False

    # Step 2: Get org ID
    org_id = await _get_org_id_by_name(org_name)
    if org_id is None:
        logger.warning(
            f"⚠️  Grafana org '{org_name}' not found — sync skipped (org may not be created yet by Terraform)"
        )
        return False

    # Step 3: Add/update user in org (using email)
    client = _get_http_client()
    url = f"{GRAFANA_BASE_URL}/api/orgs/{org_id}/users"

    try:
        response = await client.post(
            url,
            json={"loginOrEmail": user_email, "role": grafana_role},
        )
        response.raise_for_status()
        logger.info(
            f"✅ User '{username}' added to Grafana org '{org_name}' (role: {grafana_role})"
        )
        return True
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            # User already in org — update role instead
            logger.debug(
                f"📊 User '{username}' already in org '{org_name}' — updating role"
            )
            # Note: Grafana POST /api/orgs/{orgId}/users returns 409 if user
            # already exists, but the role is NOT updated automatically.
            # We need to use PATCH /api/orgs/{orgId}/users/{userId} instead.
            user_id = await _get_user_id_by_username(user_email)
            if user_id is None:
                logger.warning(
                    f"⚠️  User '{username}' not found in Grafana — sync skipped (user may not have logged in yet)"
                )
                return False

            # Update user role in org
            update_url = (
                f"{GRAFANA_BASE_URL}/api/orgs/{org_id}/users/{user_id}"
            )
            try:
                update_response = await client.patch(
                    update_url, json={"role": grafana_role}
                )
                update_response.raise_for_status()
                logger.info(
                    f"✅ User '{username}' role updated in Grafana org '{org_name}' (role: {grafana_role})"
                )
                return True
            except Exception as update_exc:
                logger.warning(
                    f"⚠️  Failed to update user '{username}' role in Grafana org '{org_name}': {update_exc}"
                )
                return False
        else:
            logger.warning(
                f"⚠️  Failed to add user '{username}' to Grafana org '{org_name}': HTTP {exc.response.status_code}"
            )
            return False
    except Exception as exc:
        logger.warning(
            f"⚠️  Failed to add user '{username}' to Grafana org '{org_name}': {exc}"
        )
        return False


async def remove_user_from_project_org(
    project_name: str, username: str
) -> bool:
    """
    Remove a user from a Grafana organization.

    If the org or user doesn't exist, the operation fails gracefully.

    Args:
        project_name: Lowercase project identifier.
        username: Keycloak username.

    Returns:
        True if successful, False otherwise.
    """
    org_name = _grafana_org_name(project_name)

    logger.info(f"📊 Removing user '{username}' from Grafana org '{org_name}'")

    # Step 1: Get org ID
    org_id = await _get_org_id_by_name(org_name)
    if org_id is None:
        logger.warning(
            f"⚠️  Grafana org '{org_name}' not found — removal skipped"
        )
        return False

    # Step 2: Get user ID
    user_id = await _get_user_id_by_username(user_email)
    if user_id is None:
        logger.warning(
            f"⚠️  Grafana user '{username}' not found — removal skipped"
        )
        return False

    # Step 3: Remove user from org
    client = _get_http_client()
    url = f"{GRAFANA_BASE_URL}/api/orgs/{org_id}/users/{user_id}"

    try:
        response = await client.delete(url)
        response.raise_for_status()
        logger.info(
            f"✅ User '{username}' removed from Grafana org '{org_name}'"
        )
        return True
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.debug(
                f"📊 User '{username}' not in Grafana org '{org_name}' — removal skipped"
            )
            return True  # Already removed, success
        else:
            logger.warning(
                f"⚠️  Failed to remove user '{username}' from Grafana org '{org_name}': HTTP {exc.response.status_code}"
            )
            return False
    except Exception as exc:
        logger.warning(
            f"⚠️  Failed to remove user '{username}' from Grafana org '{org_name}': {exc}"
        )
        return False


async def close_http_client() -> None:
    """
    Close the shared HTTP client.

    Should be called on application shutdown.
    """
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        logger.debug("📊 Grafana HTTP client closed")
