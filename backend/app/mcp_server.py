#!/usr/bin/env python3
"""
CNP Portal MCP Server

This Model Context Protocol (MCP) server exposes CNP platform documentation
and deployment APIs to AI coding assistants (Cursor, Claude Desktop, etc.).

Resources:
  - Documentation from .kiro/steering/docs/

Tools:
  - list_active_deployments: Query current deployments
  - deploy_new_app: Trigger Day-0 Kubernetes provisioning
  - get_deployment_status: Check deployment health
  - list_projects: List available projects
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from app.core.config import settings

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

# Initialize FastMCP Server
mcp = FastMCP("CNP Portal")

# Resolve paths
BASE_DIR = Path(__file__).parent.parent.parent
DOCS_DIR = BASE_DIR / ".kiro" / "steering" / "docs"
CMP_API_URL = os.getenv("CMP_API_URL", "https://cmp.3istor.com/api")

# Keycloak config — read from backend .env via pydantic-settings
KEYCLOAK_URL = settings.KEYCLOAK_URL
KEYCLOAK_REALM = "3istor"
KEYCLOAK_CLIENT_ID = settings.KEYCLOAK_CLIENT_ID
KEYCLOAK_CLIENT_SECRET = settings.KEYCLOAK_CLIENT_SECRET

# Timeout for API calls
API_TIMEOUT = 30.0

# Path where the user session (refresh token) is persisted across restarts
_SESSION_FILE = Path.home() / ".cnp_mcp_session.json"

# In-memory caches
# Service account token: (access_token, expiry_timestamp)
_service_token_cache: tuple[str, float] | None = None
# User token: (access_token, expiry_timestamp, refresh_token)
_user_token_cache: tuple[str, float, str] | None = None

TOKEN_URL = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"


def _load_session() -> None:
    """Load a persisted user session from disk into _user_token_cache."""
    global _user_token_cache
    if not _SESSION_FILE.exists():
        return
    try:
        data = json.loads(_SESSION_FILE.read_text())
        access_token = data.get("access_token", "")
        expires_at = float(data.get("expires_at", 0))
        refresh_token = data.get("refresh_token", "")
        if refresh_token:
            _user_token_cache = (access_token, expires_at, refresh_token)
    except Exception:
        pass


def _save_session(access_token: str, expires_at: float, refresh_token: str) -> None:
    """Persist user session to disk so it survives MCP restarts."""
    try:
        _SESSION_FILE.write_text(
            json.dumps(
                {
                    "access_token": access_token,
                    "expires_at": expires_at,
                    "refresh_token": refresh_token,
                }
            )
        )
        _SESSION_FILE.chmod(0o600)  # readable only by the owner
    except Exception:
        pass


def _clear_session() -> None:
    """Remove persisted session from disk and memory."""
    global _user_token_cache
    _user_token_cache = None
    if _SESSION_FILE.exists():
        _SESSION_FILE.unlink()


async def _refresh_user_token(refresh_token: str) -> tuple[str, float, str] | None:
    """Exchange a refresh token for a new access token. Returns None on failure."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": KEYCLOAK_CLIENT_ID,
                "client_secret": KEYCLOAK_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
        )
        if response.status_code != 200:
            return None
        data = response.json()
    access_token = data["access_token"]
    expires_at = time.time() + data.get("expires_in", 300)
    new_refresh = data.get("refresh_token", refresh_token)
    return access_token, expires_at, new_refresh


async def get_token() -> str:
    """
    Return the best available Bearer token:
    1. Valid user token (from login) — preferred, carries real identity + group memberships
    2. Refreshed user token (via stored refresh_token)
    3. Service account token (client_credentials fallback)
    """
    global _user_token_cache, _service_token_cache

    # Load persisted session on first call
    if _user_token_cache is None:
        _load_session()

    # Try user token first
    if _user_token_cache:
        access_token, expires_at, refresh_token = _user_token_cache
        if time.time() < expires_at - 30:
            return access_token
        # Try refresh
        refreshed = await _refresh_user_token(refresh_token)
        if refreshed:
            _user_token_cache = refreshed
            _save_session(*refreshed)
            return refreshed[0]
        # Refresh failed — clear stale session, fall through to service account
        _clear_session()

    # Fall back to service account token
    if _service_token_cache and time.time() < _service_token_cache[1] - 30:
        return _service_token_cache[0]

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": KEYCLOAK_CLIENT_ID,
                "client_secret": KEYCLOAK_CLIENT_SECRET,
            },
        )
        response.raise_for_status()
        data = response.json()

    access_token = data["access_token"]
    _service_token_cache = (access_token, time.time() + data.get("expires_in", 300))
    return access_token


# ══════════════════════════════════════════════════════════════════════════
# MCP RESOURCES (Read-only documentation)
# ══════════════════════════════════════════════════════════════════════════


@mcp.resource("docs://index")
def get_docs_index() -> str:
    """Get the main documentation index with all available documents."""
    readme_path = DOCS_DIR / "README.md"

    if not readme_path.exists():
        return "Error: Documentation index not found."

    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Add available categories
    categories = []
    for item in DOCS_DIR.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            categories.append(item.name)

    footer = f"\n\n## Available Categories\n\n"
    footer += "\n".join(f"- `{cat}/`" for cat in sorted(categories))

    return content + footer


@mcp.resource("docs://{category}/{filename}")
def get_documentation(category: str, filename: str) -> str:
    """
    Retrieve specific architectural documentation files.

    Examples:
      - docs://01-architecture/01-system-overview
      - docs://02-core-components/05-github-integration
      - docs://03-pipelines-and-workflows/01-app-provisioning-flow
    """
    # Add .md extension if not present
    if not filename.endswith(".md"):
        filename = f"{filename}.md"

    file_path = DOCS_DIR / category / filename

    if not file_path.exists():
        # Try to list available files in category
        category_path = DOCS_DIR / category
        if category_path.exists():
            available = [f.stem for f in category_path.glob("*.md")]
            return (
                f"Error: Document '{filename}' not found in '{category}'.\n\n"
                f"Available files: {', '.join(available)}"
            )
        return f"Error: Category '{category}' not found."

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@mcp.resource("docs://roadmap")
def get_roadmap() -> str:
    """Get the implementation roadmap showing project phases."""
    roadmap_path = DOCS_DIR / "README_ROADMAP.md"

    if not roadmap_path.exists():
        return "Error: Roadmap not found."

    with open(roadmap_path, "r", encoding="utf-8") as f:
        return f.read()


# ══════════════════════════════════════════════════════════════════════════
# MCP TOOLS (API actions)
# ══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def list_active_deployments() -> str:
    """
    List all current application deployments registered in the portal.

    Returns:
        JSON string with deployment list or error message
    """
    headers = {"Authorization": f"Bearer {await get_token()}"}

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"{CMP_API_URL}/deployments", headers=headers
            )

            if response.status_code == 401:
                return "Error: Authentication failed. Token may be expired."

            if response.status_code != 200:
                return f"Error: Failed to fetch deployments (HTTP {response.status_code})"

            data = response.json()

            # Format response
            result = {
                "total": len(data),
                "deployments": [
                    {
                        "id": d["id"],
                        "name": d["name"],
                        "status": d["status"],
                        "provider_type": d.get(
                            "provider_type", "legacy_hybrid"
                        ),
                        "project_id": d.get("project_id"),
                        "github_repo_url": d.get("github_repo_url"),
                        "argocd_app_name": d.get("argocd_app_name"),
                    }
                    for d in data
                ],
            }

            return json.dumps(result, indent=2)

    except httpx.TimeoutException:
        return "Error: Request timed out. CMP API may be unavailable."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def get_deployment_status(deployment_id: int) -> str:
    """
    Get detailed status of a specific deployment.

    Args:
        deployment_id: ID of the deployment to query

    Returns:
        JSON string with deployment details
    """
    headers = {"Authorization": f"Bearer {await get_token()}"}

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"{CMP_API_URL}/deployments/{deployment_id}", headers=headers
            )

            if response.status_code == 404:
                return f"Error: Deployment {deployment_id} not found."

            if response.status_code != 200:
                return f"Error: Failed to fetch deployment (HTTP {response.status_code})"

            return response.text

    except httpx.TimeoutException:
        return "Error: Request timed out."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def list_projects() -> str:
    """
    List all available projects.

    Returns:
        JSON string with project list
    """
    headers = {"Authorization": f"Bearer {await get_token()}"}

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"{CMP_API_URL}/projects", headers=headers
            )

            if response.status_code != 200:
                return f"Error: Failed to fetch projects (HTTP {response.status_code})"

            return response.text

    except httpx.TimeoutException:
        return "Error: Request timed out."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def deploy_new_app(
    name: str,
    project_name: str,
    template_id: str = "kubernetes-fastapi",
    github_installation_id: str = "12345678",
    replica_count: int = 2,
    sso_protected: bool = False,
) -> str:
    """
    Trigger Day-0 provisioning of a new Kubernetes GitOps application.

    Args:
        name: Application name (e.g., 'billing-web')
        project_name: Project/team name (e.g., 'project-alpha')
        template_id: Template to use (default: 'kubernetes-fastapi')
        github_installation_id: GitHub App installation ID
        replica_count: Number of pod replicas (1-10)
        sso_protected: Enable Keycloak SSO protection

    Returns:
        JSON string with deployment creation result
    """
    headers = {
        "Authorization": f"Bearer {await get_token()}",
        "Content-Type": "application/json",
    }

    payload = {
        "name": name,
        "template_id": template_id,
        "provider_type": "kubernetes",
        "app_config": {
            "project_name": project_name,
            "github_installation_id": github_installation_id,
            "replica_count": replica_count,
            "sso_protected": sso_protected,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                f"{CMP_API_URL}/deployments", headers=headers, json=payload
            )

            if response.status_code == 400:
                return f"Error: Invalid request. {response.text}"

            if response.status_code == 401:
                return "Error: Authentication failed."

            if response.status_code != 201:
                return f"Error: Failed to create deployment (HTTP {response.status_code}): {response.text}"

            data = response.json()
            return json.dumps(
                {
                    "status": "success",
                    "message": f"Deployment '{name}' created successfully",
                    "deployment_id": data.get("id"),
                    "deployment_status": data.get("status"),
                    "next_steps": [
                        "Monitor deployment status with get_deployment_status",
                        f"Check GitHub repo once status is 'running'",
                        f"View in ArgoCD: https://argocd.3istor.com",
                    ],
                },
                indent=2,
            )

    except httpx.TimeoutException:
        return "Error: Request timed out. Deployment may still be processing."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def delete_deployment(deployment_id: int) -> str:
    """
    Delete a deployment and all associated resources.

    Warning: This action is irreversible!

    Args:
        deployment_id: ID of the deployment to delete

    Returns:
        JSON string with deletion result
    """
    headers = {"Authorization": f"Bearer {await get_token()}"}

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.delete(
                f"{CMP_API_URL}/deployments/{deployment_id}", headers=headers
            )

            if response.status_code == 404:
                return f"Error: Deployment {deployment_id} not found."

            if response.status_code != 200:
                return f"Error: Failed to delete deployment (HTTP {response.status_code})"

            return json.dumps(
                {
                    "status": "success",
                    "message": f"Deployment {deployment_id} deletion initiated",
                    "note": "Terraform destroy is running in background",
                },
                indent=2,
            )

    except httpx.TimeoutException:
        return "Error: Request timed out. Deletion may still be processing."
    except Exception as e:
        return f"Error: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════
# ONLINE DOCUMENTATION
# ══════════════════════════════════════════════════════════════════════════

ONLINE_DOCS_URL = "https://3-istor.github.io/cnp-docs/"


@mcp.tool()
async def fetch_online_documentation(section: str = "") -> str:
    """
    Fetch the CNP online documentation from https://3-istor.github.io/cnp-docs/

    Use this to answer questions about the platform, its architecture,
    how to use it, and its features. Prefer this over local docs for
    user-facing guidance.

    Args:
        section: Optional URL fragment/anchor to fetch a specific section
                 (e.g. "core-objectives", "architecture", "getting-started").
                 Leave empty to get the full documentation index.

    Returns:
        Documentation content as plain text extracted from the page.
    """
    import re

    url = ONLINE_DOCS_URL
    if section:
        url = f"{ONLINE_DOCS_URL}#{section}"

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(ONLINE_DOCS_URL)
            if response.status_code != 200:
                return f"Error: Could not fetch documentation (HTTP {response.status_code})"

            html = response.text

            # Strip scripts, styles, nav, footer
            html = re.sub(r"<(script|style|nav|footer)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
            # Strip all HTML tags
            text = re.sub(r"<[^>]+>", " ", html)
            # Collapse whitespace
            text = re.sub(r"\s{2,}", "\n", text).strip()

            # If a section is requested, try to find it in the text
            if section:
                section_clean = section.replace("-", " ").lower()
                lines = text.splitlines()
                start = next(
                    (i for i, l in enumerate(lines) if section_clean in l.lower()),
                    None,
                )
                if start is not None:
                    text = "\n".join(lines[start : start + 100])

            return text[:8000]  # Cap at 8k chars to stay within context limits

    except httpx.TimeoutException:
        return "Error: Documentation site did not respond in time."
    except Exception as e:
        return f"Error: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════
# IDENTITY
# ══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def start_login() -> str:
    """
    Start the login flow for a real CNP user account (supports 2FA).

    This uses the OAuth2 Device Authorization Flow:
    1. Call this tool → get a short URL to open in your browser
    2. Open the URL, authenticate normally (including 2FA if enabled)
    3. Call complete_login() to finish and store your session

    Once logged in, all tools use your real identity and group memberships.
    The session persists across MCP restarts via a refresh token.
    """
    device_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth/device"

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            device_url,
            data={
                "client_id": KEYCLOAK_CLIENT_ID,
                "client_secret": KEYCLOAK_CLIENT_SECRET,
                "scope": "openid profile email offline_access groups",
            },
        )

    if response.status_code != 200:
        return f"Error: Could not start device flow (HTTP {response.status_code}): {response.text[:200]}"

    data = response.json()

    # Store device_code in session file temporarily so complete_login can find it
    try:
        _SESSION_FILE.write_text(json.dumps({"pending_device_code": data["device_code"]}))
    except Exception:
        pass

    return json.dumps(
        {
            "status": "pending",
            "instruction": "Ouvre cette URL dans ton navigateur et connecte-toi avec ton compte (2FA inclus) :",
            "url": data.get("verification_uri_complete") or data.get("verification_uri"),
            "user_code": data.get("user_code"),
            "expires_in_seconds": data.get("expires_in", 300),
            "next_step": "Une fois connecté dans le navigateur, dis-moi 'c'est fait' pour appeler complete_login()",
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
async def complete_login() -> str:
    """
    Complete the login after the user has authenticated in the browser.

    Call this after start_login() once you've authenticated in the browser.
    Polls Keycloak for up to 90 seconds then returns success or timeout.
    """
    global _user_token_cache
    import base64

    # Read pending device_code from session file
    try:
        session_data = json.loads(_SESSION_FILE.read_text())
        device_code = session_data.get("pending_device_code")
    except Exception:
        device_code = None

    if not device_code:
        return "Error: No pending login found. Call start_login() first."

    token_url = TOKEN_URL
    grant_type = "urn:ietf:params:oauth:grant-type:device_code"

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(18):  # poll up to 90s (18 × 5s)
            await asyncio.sleep(5)
            response = await client.post(
                token_url,
                data={
                    "grant_type": grant_type,
                    "client_id": KEYCLOAK_CLIENT_ID,
                    "client_secret": KEYCLOAK_CLIENT_SECRET,
                    "device_code": device_code,
                },
            )
            data = response.json()

            if response.status_code == 200:
                access_token = data["access_token"]
                expires_at = time.time() + data.get("expires_in", 300)
                refresh_token = data.get("refresh_token", "")

                _user_token_cache = (access_token, expires_at, refresh_token)
                if refresh_token:
                    _save_session(access_token, expires_at, refresh_token)

                # Decode claims
                parts = access_token.split(".")
                payload = base64.urlsafe_b64decode(parts[1] + "=" * (4 - len(parts[1]) % 4))
                claims = json.loads(payload)

                return json.dumps(
                    {
                        "status": "logged_in",
                        "username": claims.get("preferred_username"),
                        "name": claims.get("name"),
                        "email": claims.get("email"),
                        "groups": claims.get("groups", []),
                        "roles": claims.get("realm_access", {}).get("roles", []),
                        "session_persisted": bool(refresh_token),
                    },
                    indent=2,
                    ensure_ascii=False,
                )

            error = data.get("error", "")
            if error == "authorization_pending":
                continue  # still waiting
            if error == "slow_down":
                await asyncio.sleep(5)
                continue
            if error in ("expired_token", "access_denied"):
                _clear_session()
                return f"Login cancelled or expired: {error}. Call start_login() to try again."
            return f"Error: {error} — {data.get('error_description', '')}"

    return "Timeout: authentication not completed within 90 seconds. Call start_login() to try again."


@mcp.tool()
async def logout() -> str:
    """
    Log out of the CNP platform and remove the persisted session.

    After logout, the MCP falls back to the generic service account.
    """
    had_session = _user_token_cache is not None or _SESSION_FILE.exists()
    _clear_session()

    if had_session:
        return json.dumps({"status": "logged_out", "message": "Session utilisateur supprimée. Retour au service account."}, ensure_ascii=False)
    return json.dumps({"status": "no_session", "message": "Aucune session active."}, ensure_ascii=False)


@mcp.tool()
async def get_current_user_info() -> str:
    """
    Return information about the currently authenticated identity used by
    this MCP server to communicate with the CNP platform.

    Returns username, email, roles and project memberships from the JWT.
    Also lists accessible projects via the API.
    """
    import base64

    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}

    try:
        # Decode JWT payload without verification (same approach as the backend)
        parts = token.split(".")
        if len(parts) != 3:
            return "Error: Invalid JWT format"
        padding = 4 - len(parts[1]) % 4
        payload_json = base64.urlsafe_b64decode(parts[1] + "=" * padding)
        claims = json.loads(payload_json)

        username = claims.get("preferred_username") or claims.get("sub", "")
        is_service_account = "service-account" in username

        result: dict = {
            "username": username,
            "client_id": claims.get("azp") or claims.get("client_id"),
            "roles": claims.get("realm_access", {}).get("roles", []),
            "groups": claims.get("groups", []),
            "token_expires_at": claims.get("exp"),
            "authentication_type": (
                "service_account (client_credentials)"
                if is_service_account
                else "user_token"
            ),
        }
        if not is_service_account:
            result["email"] = claims.get("email")
            result["name"] = claims.get("name")

        # Fetch accessible projects from the API
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            projects_response = await client.get(
                f"{CMP_API_URL}/projects", headers=headers
            )
            if projects_response.status_code == 200:
                try:
                    result["accessible_projects"] = [
                        p.get("name", p) for p in projects_response.json()
                    ]
                except Exception:
                    pass

        return json.dumps(result, indent=2)

    except Exception as e:
        return f"Error: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════
# FINOPS
# ══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_finops_overview(project: str = "", period: str = "30d") -> str:
    """
    Get FinOps cost overview: total spend, budget status, per-resource
    breakdown (CPU/RAM/storage/network) and daily cost timeline.

    Use this to generate cost reports or advise on budget health.

    Args:
        project: Project name to filter (e.g. "sandbox"). Empty = all accessible projects.
        period:  Time window — "7d", "30d", "90d", or "1y". Default: "30d".

    Returns:
        JSON with summary KPIs, budget status, app list and cost timeline.
    """
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}
    params: dict = {"period": period, "granularity": "daily"}
    if project:
        params["project"] = project

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"{CMP_API_URL}/finops/overview", headers=headers, params=params
            )
            if response.status_code == 403:
                return (
                    "Access denied: the MCP service account does not have FinOps access. "
                    "Ask an admin to add 'service-account-3-istor-openid' to FINOPS_ADMIN_USERS "
                    "in backend/.env, or grant it the 'cnp-admin' realm role in Keycloak."
                )
            if response.status_code != 200:
                return f"Error: FinOps API returned HTTP {response.status_code}: {response.text[:200]}"
            return json.dumps(response.json(), indent=2)

    except httpx.TimeoutException:
        return "Error: FinOps API timed out."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def get_finops_apps(project: str = "") -> str:
    """
    Get per-application cost breakdown with monthly spend, daily rate
    and resource split (CPU/RAM/storage/network).

    Use this to identify the most expensive apps or compare costs across projects.

    Args:
        project: Project name to filter. Empty = all accessible projects.

    Returns:
        JSON list of apps with their cost metrics.
    """
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}
    params = {"project": project} if project else {}

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"{CMP_API_URL}/finops/apps", headers=headers, params=params
            )
            if response.status_code == 403:
                return (
                    "Access denied: the MCP service account does not have FinOps access. "
                    "Ask an admin to add 'service-account-3-istor-openid' to FINOPS_ADMIN_USERS "
                    "in backend/.env."
                )
            if response.status_code != 200:
                return f"Error: HTTP {response.status_code}: {response.text[:200]}"
            return json.dumps(response.json(), indent=2)

    except httpx.TimeoutException:
        return "Error: Request timed out."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def get_finops_recommendations(project: str = "") -> str:
    """
    Get cost optimisation recommendations with estimated monthly savings.

    Each recommendation includes: what to change, current vs recommended
    config, monthly saving in EUR, confidence level, and implementation effort.

    Use this to advise users on how to reduce their cloud costs.

    Args:
        project: Project name to filter. Empty = all accessible projects.

    Returns:
        JSON list of recommendations sorted by potential saving.
    """
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}
    params = {"project": project} if project else {}

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"{CMP_API_URL}/finops/recommendations",
                headers=headers,
                params=params,
            )
            if response.status_code == 403:
                return (
                    "Access denied: the MCP service account does not have FinOps access. "
                    "Ask an admin to add 'service-account-3-istor-openid' to FINOPS_ADMIN_USERS "
                    "in backend/.env."
                )
            if response.status_code != 200:
                return f"Error: HTTP {response.status_code}: {response.text[:200]}"

            recs = response.json()
            # Sort by saving descending for better AI prioritisation
            if isinstance(recs, list):
                recs.sort(key=lambda r: r.get("monthly_saving_eur", 0), reverse=True)
            return json.dumps(recs, indent=2)

    except httpx.TimeoutException:
        return "Error: Request timed out."
    except Exception as e:
        return f"Error: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════
# SERVER ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Verify documentation directory exists
    if not DOCS_DIR.exists():
        print(f"Warning: Documentation directory not found: {DOCS_DIR}")
        print("MCP resources will return errors.")

    print(f"🚀 Starting CNP Portal MCP Server")
    print(f"📚 Documentation path: {DOCS_DIR}")
    print(f"🔌 CMP API URL: {CMP_API_URL}")
    print(f"")
    print(f"Available resources:")
    print(f"  - docs://index")
    print(f"  - docs://roadmap")
    print(f"  - docs://<category>/<filename>")
    print(f"")
    print(f"Available tools:")
    print(f"  - list_active_deployments")
    print(f"  - get_deployment_status")
    print(f"  - list_projects")
    print(f"  - deploy_new_app")
    print(f"  - delete_deployment")
    print(f"  - start_login / complete_login / logout")
    print(f"  - get_current_user_info")
    print(f"  - fetch_online_documentation ({ONLINE_DOCS_URL})")
    print(f"  - get_finops_overview")
    print(f"  - get_finops_apps")
    print(f"  - get_finops_recommendations")
    print(f"")

    # Run MCP server over stdio
    mcp.run()
