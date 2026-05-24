"""
GitHub App Integration Service

Handles JWT generation and Installation Access Token exchange for the CNP GitHub App.
This allows the CMP to dynamically create repositories on behalf of users.

GitHub App ID: 3836905
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

GITHUB_APP_ID = "3836905"
GITHUB_API_BASE = "https://api.github.com"


class GitHubAppError(Exception):
    """Raised when GitHub App operations fail."""


def generate_jwt() -> str:
    """
    Generate a JSON Web Token (JWT) signed with the CNP GitHub App Private Key.

    The JWT is valid for 10 minutes and is used to authenticate as the GitHub App
    itself (not as an installation).

    Returns:
        str: The signed JWT token

    Raises:
        GitHubAppError: If the private key is not configured
    """
    if not settings.GITHUB_APP_PRIVATE_KEY:
        raise GitHubAppError(
            "GITHUB_APP_PRIVATE_KEY not configured in environment"
        )

    now = datetime.now(timezone.utc)
    payload = {
        "iat": int(now.timestamp()),  # Issued at
        "exp": int((now + timedelta(minutes=10)).timestamp()),  # Expires in 10 min
        "iss": GITHUB_APP_ID,  # Issuer (GitHub App ID)
    }

    try:
        token = jwt.encode(
            payload,
            settings.GITHUB_APP_PRIVATE_KEY,
            algorithm="RS256"
        )
        logger.debug("Generated GitHub App JWT (expires in 10 minutes)")
        return token
    except Exception as exc:
        raise GitHubAppError(f"Failed to generate JWT: {exc}") from exc


async def get_installation_token(installation_id: str) -> str:
    """
    Exchange the GitHub App JWT for a short-lived Installation Access Token.

    This token is scoped to a specific user/organization installation and has
    the permissions defined in the GitHub App configuration.

    Args:
        installation_id: The GitHub App installation ID (stored in Keycloak user profile)

    Returns:
        str: The installation access token (valid for 1 hour)

    Raises:
        GitHubAppError: If the token exchange fails
    """
    app_jwt = generate_jwt()

    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, timeout=10.0)
            response.raise_for_status()

            data = response.json()
            token = data.get("token")

            if not token:
                raise GitHubAppError("No token in GitHub API response")

            logger.info(
                "Successfully obtained installation token for installation %s",
                installation_id
            )
            return token

    except httpx.HTTPStatusError as exc:
        logger.error(
            "GitHub API returned %s: %s",
            exc.response.status_code,
            exc.response.text
        )
        raise GitHubAppError(
            f"Failed to get installation token: {exc.response.status_code}"
        ) from exc
    except Exception as exc:
        raise GitHubAppError(
            f"Failed to communicate with GitHub API: {exc}"
        ) from exc


async def create_repository(
    installation_token: str,
    repo_name: str,
    org_name: str | None = None,
    private: bool = True,
    description: str = "Application provisioned by CNP"
) -> dict:
    """
    Create a new GitHub repository using the Installation Access Token.

    Args:
        installation_token: The short-lived installation token
        repo_name: Name of the repository to create
        org_name: Organization name (if None, creates in user's personal account)
        private: Whether the repository should be private
        description: Repository description

    Returns:
        dict: Repository metadata including 'html_url', 'clone_url', 'full_name'

    Raises:
        GitHubAppError: If repository creation fails
    """
    if org_name:
        url = f"{GITHUB_API_BASE}/orgs/{org_name}/repos"
    else:
        url = f"{GITHUB_API_BASE}/user/repos"

    headers = {
        "Authorization": f"Bearer {installation_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    payload = {
        "name": repo_name,
        "description": description,
        "private": private,
        "auto_init": False,  # We'll push the template code ourselves
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, headers=headers, json=payload, timeout=15.0
            )
            response.raise_for_status()

            repo_data = response.json()
            logger.info(
                "Created repository: %s",
                repo_data.get("full_name")
            )
            return repo_data

    except httpx.HTTPStatusError as exc:
        error_msg = exc.response.json().get("message", exc.response.text)
        logger.error(
            "Failed to create repository %s: %s",
            repo_name,
            error_msg
        )
        raise GitHubAppError(
            f"Repository creation failed: {error_msg}"
        ) from exc
    except Exception as exc:
        raise GitHubAppError(
            f"Failed to create repository: {exc}"
        ) from exc
