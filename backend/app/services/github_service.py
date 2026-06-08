"""
GitHub App Integration Service

Handles JWT generation and Installation Access Token exchange for the CNP GitHub App.
This allows the CMP to dynamically create repositories on behalf of users.

GitHub App ID: 3836905
"""

import base64
import logging
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


async def get_file_content(
    installation_token: str,
    repo_full_name: str,
    file_path: str,
    ref: str = "main",
) -> tuple[str, str]:
    """
    Fetch the decoded content and current SHA of a file from a GitHub repository.

    The SHA is required for subsequent update calls to avoid 409 Conflict errors.

    Args:
        installation_token: Short-lived GitHub installation access token.
        repo_full_name:      ``owner/repo`` (e.g. ``"3-istor/my-app"``).
        file_path:           Path inside the repository (e.g. ``"deploy/values.yaml"``).
        ref:                 Branch, tag, or commit SHA to read from (default: ``"main"``).

    Returns:
        Tuple of ``(decoded_content: str, sha: str)``.

    Raises:
        GitHubAppError: On HTTP errors or missing fields in the GitHub response.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {installation_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url, headers=headers, params={"ref": ref}, timeout=15.0
            )

            if response.status_code == 404:
                raise GitHubAppError(
                    f"File not found: '{file_path}' in repository '{repo_full_name}'"
                )

            response.raise_for_status()
            data = response.json()

            raw_content: str = data.get("content", "")
            sha: str = data.get("sha", "")

            if not raw_content or not sha:
                raise GitHubAppError(
                    f"GitHub response missing 'content' or 'sha' for file '{file_path}'"
                )

            # GitHub returns content as base64 with newlines – strip them before decoding
            decoded = base64.b64decode(raw_content.replace("\n", "")).decode("utf-8")

            logger.info(
                "Fetched file '%s' from '%s' (sha: %.8s)", file_path, repo_full_name, sha
            )
            return decoded, sha

    except GitHubAppError:
        raise
    except httpx.HTTPStatusError as exc:
        error_msg = exc.response.json().get("message", exc.response.text)
        logger.error("GitHub API error fetching '%s': %s", file_path, error_msg)
        raise GitHubAppError(
            f"Failed to fetch file '{file_path}': {error_msg}"
        ) from exc
    except Exception as exc:
        raise GitHubAppError(
            f"Unexpected error fetching file '{file_path}': {exc}"
        ) from exc


async def update_file_content(
    installation_token: str,
    repo_full_name: str,
    file_path: str,
    content: str,
    message: str,
    sha: str,
    branch: str = "main",
) -> dict:
    """
    Commit and push updated content for a file in a GitHub repository.

    Uses the GitHub Contents API (PUT). The ``sha`` of the *current* file
    version is mandatory — GitHub will reject the request with 409 Conflict
    if it doesn't match.

    Args:
        installation_token: Short-lived GitHub installation access token.
        repo_full_name:     ``owner/repo``.
        file_path:          Path inside the repository.
        content:            New raw file content (UTF-8 string).
        message:            Git commit message.
        sha:                SHA of the file version being replaced (from ``get_file_content``).
        branch:             Target branch (default: ``"main"``).

    Returns:
        dict: GitHub API response containing ``commit`` and ``content`` metadata.

    Raises:
        GitHubAppError: On HTTP errors, including 409 Conflict (stale SHA).
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {installation_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")

    payload = {
        "message": message,
        "content": encoded_content,
        "sha": sha,
        "branch": branch,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                url, headers=headers, json=payload, timeout=20.0
            )

            if response.status_code == 409:
                raise GitHubAppError(
                    "Conflict (409): the file SHA is outdated. "
                    "Another commit may have modified this file — please reload and retry."
                )

            response.raise_for_status()
            data = response.json()

            commit_sha = data.get("commit", {}).get("sha", "")
            logger.info(
                "Committed '%s' to '%s' on branch '%s' (commit: %.8s)",
                file_path,
                repo_full_name,
                branch,
                commit_sha,
            )
            return data

    except GitHubAppError:
        raise
    except httpx.HTTPStatusError as exc:
        error_msg = exc.response.json().get("message", exc.response.text)
        logger.error("GitHub API error updating '%s': %s", file_path, error_msg)
        raise GitHubAppError(
            f"Failed to update file '{file_path}': {error_msg}"
        ) from exc
    except Exception as exc:
        raise GitHubAppError(
            f"Unexpected error updating file '{file_path}': {exc}"
        ) from exc
