"""
GitHub App Integration Service

Handles JWT generation and Installation Access Token exchange for the CNP GitHub App.
This lets the CMP read and commit files in repositories on behalf of users
(app repositories themselves are created by Terraform).

GitHub App ID: 3836905
"""

import base64
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

GITHUB_APP_ID = "3836905"
GITHUB_API_BASE = "https://api.github.com"


class GitHubAppError(Exception):
    """Raised when GitHub App operations fail."""


def _github_error_message(response: httpx.Response) -> str:
    # Gateway errors (502/503/504) and some 403s come back with an empty or HTML body.
    try:
        message = response.json().get("message")
    except ValueError:
        message = None
    return f"HTTP {response.status_code}: {message or response.text[:200] or response.reason_phrase}"


class FileNotInRepoError(GitHubAppError):
    """Raised when a requested file does not exist in the repository.

    Subclasses GitHubAppError so existing callers that catch the base class are
    unaffected; callers that need to tell "absent" from "broken" catch this.
    """


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
        "exp": int(
            (now + timedelta(minutes=10)).timestamp()
        ),  # Expires in 10 min
        "iss": GITHUB_APP_ID,  # Issuer (GitHub App ID)
    }

    try:
        token = jwt.encode(
            payload, settings.GITHUB_APP_PRIVATE_KEY, algorithm="RS256"
        )
        logger.debug("Generated GitHub App JWT (expires in 10 minutes)")
        return token
    except Exception as exc:
        raise GitHubAppError(f"Failed to generate JWT: {exc}") from exc


async def _get_installation_token(installation_id: str) -> str:
    """
    Exchange the GitHub App JWT for an installation token with every
    permission the app holds on every repository of the installation.

    Only for the 3-Istor org installation, through
    ``get_org_installation_token``. A user's installation must go through
    ``get_scoped_installation_token``.

    Raises:
        GitHubAppError: If the token exchange fails
    """
    return await _request_installation_token(installation_id, None)


async def get_org_installation_token() -> str:
    """
    Installation token of the CNP-Portal app on the platform org.

    App repositories live in the org and are created by the CMP, so this is
    the only installation used for them. An installation ID stored in a
    deployment or sent by a client is never used to mint a token.

    Raises:
        GitHubAppError: If GITHUB_INSTALLATION_ID is not set or the exchange fails
    """
    if not settings.GITHUB_INSTALLATION_ID:
        raise GitHubAppError("GITHUB_INSTALLATION_ID is not configured")
    return await _get_installation_token(settings.GITHUB_INSTALLATION_ID)


_PERMISSION_LEVELS = frozenset({"read", "write"})
# GitHub refuses more than 500 repositories in one token request.
_MAX_SCOPED_REPOSITORIES = 500


async def get_scoped_installation_token(
    installation_id: str,
    repositories: list[str],
    permissions: dict[str, str],
) -> str:
    """
    Installation token limited to ``repositories`` (names, without owner) and
    ``permissions`` (e.g. ``{"contents": "read", "metadata": "read"}``).

    Every token minted on an installation the user controls must go through
    here, so a token never carries the app's full permission set. No caller
    yet: groundwork for the GitHub OAuth linking flow (CNP-241).

    Raises:
        GitHubAppError: If the scope is empty or malformed, or the exchange fails
    """
    if not repositories:
        raise GitHubAppError("A scoped token needs at least one repository")
    if len(repositories) > _MAX_SCOPED_REPOSITORIES:
        raise GitHubAppError(
            f"A scoped token covers at most {_MAX_SCOPED_REPOSITORIES} "
            f"repositories, got {len(repositories)}"
        )
    if not permissions:
        raise GitHubAppError("A scoped token needs explicit permissions")
    bad_names = [r for r in repositories if not r or "/" in r]
    if bad_names:
        raise GitHubAppError(
            f"Repository names must not include the owner: {bad_names}"
        )
    bad_levels = {
        k: v for k, v in permissions.items() if v not in _PERMISSION_LEVELS
    }
    if bad_levels:
        raise GitHubAppError(f"Invalid permission levels: {bad_levels}")
    return await _request_installation_token(
        installation_id,
        {"repositories": list(repositories), "permissions": dict(permissions)},
    )


async def get_repository_id_scoped_token(
    installation_id: str,
    repository_ids: list[int],
    permissions: dict[str, str],
) -> str:
    """
    Like get_scoped_installation_token, by repository id: a repository
    renamed, or deleted and recreated under the same name, is not covered.

    Raises:
        GitHubAppError: If the scope is empty or malformed, or the exchange fails
    """
    if not repository_ids or len(repository_ids) > _MAX_SCOPED_REPOSITORIES:
        raise GitHubAppError(
            f"A scoped token covers 1 to {_MAX_SCOPED_REPOSITORIES} "
            "repositories"
        )
    if any(
        isinstance(i, bool) or not isinstance(i, int) or i <= 0
        for i in repository_ids
    ):
        raise GitHubAppError(f"Invalid repository ids: {repository_ids}")
    if not permissions or any(
        v not in _PERMISSION_LEVELS for v in permissions.values()
    ):
        raise GitHubAppError(f"Invalid permissions: {permissions}")
    return await _request_installation_token(
        installation_id,
        {
            "repository_ids": list(repository_ids),
            "permissions": dict(permissions),
        },
    )


async def _request_installation_token(
    installation_id: str, body: dict | None
) -> str:
    app_jwt = generate_jwt()

    url = (
        f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    )
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, headers=headers, json=body, timeout=10.0
            )
            response.raise_for_status()

            data = response.json()
            token = data.get("token")

            if not token:
                raise GitHubAppError("No token in GitHub API response")

            logger.info(
                "Successfully obtained installation token for installation %s",
                installation_id,
            )
            return token

    except httpx.HTTPStatusError as exc:
        logger.error(
            "GitHub API returned %s: %s",
            exc.response.status_code,
            exc.response.text,
        )
        raise GitHubAppError(
            f"Failed to get installation token: {exc.response.status_code}"
        ) from exc
    except GitHubAppError:
        raise
    except Exception as exc:
        raise GitHubAppError(
            f"Failed to communicate with GitHub API: {exc}"
        ) from exc


async def create_repository(
    installation_token: str,
    repo_name: str,
    org_name: str | None = None,
    private: bool = True,
    description: str = "Application provisioned by CNP",
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
            logger.info("Created repository: %s", repo_data.get("full_name"))
            return repo_data

    except httpx.HTTPStatusError as exc:
        error_msg = _github_error_message(exc.response)
        logger.error(
            "Failed to create repository %s: %s", repo_name, error_msg
        )
        raise GitHubAppError(
            f"Repository creation failed: {error_msg}"
        ) from exc
    except Exception as exc:
        raise GitHubAppError(f"Failed to create repository: {exc}") from exc


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
                raise FileNotInRepoError(
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
            decoded = base64.b64decode(raw_content.replace("\n", "")).decode(
                "utf-8"
            )

            logger.info(
                "Fetched file '%s' from '%s' (sha: %.8s)",
                file_path,
                repo_full_name,
                sha,
            )
            return decoded, sha

    except GitHubAppError:
        raise
    except httpx.HTTPStatusError as exc:
        error_msg = _github_error_message(exc.response)
        logger.error(
            "GitHub API error fetching '%s': %s", file_path, error_msg
        )
        raise GitHubAppError(
            f"Failed to fetch file '{file_path}': {error_msg}"
        ) from exc
    except Exception as exc:
        raise GitHubAppError(
            f"Unexpected error fetching file '{file_path}': {exc}"
        ) from exc


async def list_directory(
    installation_token: str,
    repo_full_name: str,
    dir_path: str,
    ref: str = "main",
) -> list[str]:
    """
    List the file names directly under a directory of a GitHub repository.

    Returns an empty list when the directory does not exist.

    Raises:
        GitHubAppError: On HTTP errors or an unexpected response shape.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{dir_path}"
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
                return []
            response.raise_for_status()
            entries = response.json()
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to list '{dir_path}' in '{repo_full_name}': {exc}"
        ) from exc

    if not isinstance(entries, list):
        raise GitHubAppError(
            f"'{dir_path}' in '{repo_full_name}' is not a directory"
        )
    return [e["name"] for e in entries if e.get("type") == "file"]


async def put_file_content(
    installation_token: str,
    repo_full_name: str,
    file_path: str,
    content: str,
    message: str,
    sha: str | None = None,
    branch: str = "main",
) -> dict:
    """
    Create or replace a file in a GitHub repository.

    ``sha`` is optional: omit it to create a file that does not exist yet,
    pass it (from ``get_file_content``) to replace one that does, GitHub
    rejects a stale one with 409 Conflict.

    Args:
        installation_token: Short-lived GitHub installation access token.
        repo_full_name:     ``owner/repo``.
        file_path:          Path inside the repository.
        content:            Raw file content (UTF-8 string).
        message:            Git commit message.
        sha:                SHA of the version being replaced, or ``None`` to create.
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

    payload: dict = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                url, headers=headers, json=payload, timeout=20.0
            )

            if response.status_code == 409:
                raise GitHubAppError(
                    f"Conflict (409) writing '{file_path}': the file SHA is "
                    "outdated. Another commit modified it, re-read and retry."
                )

            response.raise_for_status()
            data = response.json()

            logger.info(
                "Committed '%s' to '%s' on branch '%s' (commit: %.8s)",
                file_path,
                repo_full_name,
                branch,
                data.get("commit", {}).get("sha", ""),
            )
            return data

    except GitHubAppError:
        raise
    except httpx.HTTPStatusError as exc:
        error_msg = _github_error_message(exc.response)
        raise GitHubAppError(
            f"Failed to write file '{file_path}': {error_msg}"
        ) from exc
    except Exception as exc:
        raise GitHubAppError(
            f"Unexpected error writing file '{file_path}': {exc}"
        ) from exc


async def delete_file(
    installation_token: str,
    repo_full_name: str,
    file_path: str,
    message: str,
    sha: str,
    branch: str = "main",
) -> dict:
    """
    Delete a file from a GitHub repository.

    Args:
        installation_token: Short-lived GitHub installation access token.
        repo_full_name:     ``owner/repo``.
        file_path:          Path inside the repository.
        message:            Git commit message.
        sha:                SHA of the file being deleted (from ``get_file_content``).
        branch:             Target branch (default: ``"main"``).

    Returns:
        dict: GitHub API response containing ``commit`` metadata.

    Raises:
        GitHubAppError: On HTTP errors.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {installation_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                "DELETE",
                url,
                headers=headers,
                json={"message": message, "sha": sha, "branch": branch},
                timeout=20.0,
            )
            response.raise_for_status()
            data = response.json()

            logger.info(
                "Deleted '%s' from '%s' (commit: %.8s)",
                file_path,
                repo_full_name,
                data.get("commit", {}).get("sha", ""),
            )
            return data

    except httpx.HTTPStatusError as exc:
        error_msg = _github_error_message(exc.response)
        raise GitHubAppError(
            f"Failed to delete file '{file_path}': {error_msg}"
        ) from exc
    except Exception as exc:
        raise GitHubAppError(
            f"Unexpected error deleting file '{file_path}': {exc}"
        ) from exc


@dataclass(frozen=True)
class Artifact:
    id: str
    created_at: datetime
    content: dict | None


def _api_headers(installation_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {installation_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_default_branch(
    installation_token: str, repo_full_name: str
) -> str:
    """
    Raises:
        GitHubAppError: On HTTP errors.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}",
                headers=_api_headers(installation_token),
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to read repository '{repo_full_name}': {exc}"
        ) from exc
    return response.json().get("default_branch", "main")


async def get_latest_artifact(
    installation_token: str,
    repo_full_name: str,
    artifact_name: str,
    file_name: str,
    branch: str,
    known_id: str | None = None,
) -> Artifact | None:
    """
    The newest non-expired artifact named *artifact_name* produced on
    *branch*, with *file_name* parsed as JSON. The archive is not downloaded
    when the artifact is *known_id*: its content is then None.

    Returns None when there is no such artifact.

    Raises:
        GitHubAppError: On HTTP errors or an unreadable archive.
    """
    headers = _api_headers(installation_token)
    list_url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/artifacts"

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(
                list_url,
                headers=headers,
                params={"name": artifact_name, "per_page": 30},
                timeout=15.0,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            artifacts = [
                a
                for a in response.json().get("artifacts", [])
                if not a.get("expired")
                and (a.get("workflow_run") or {}).get("head_branch") == branch
            ]
            if not artifacts:
                return None
            newest = max(artifacts, key=lambda a: a.get("created_at", ""))
            artifact_id = str(newest["id"])
            created_at = datetime.fromisoformat(
                newest["created_at"].replace("Z", "+00:00")
            )
            if artifact_id == known_id:
                return Artifact(artifact_id, created_at, None)
            archive = await client.get(
                newest["archive_download_url"], headers=headers, timeout=30.0
            )
            archive.raise_for_status()
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to fetch artifact '{artifact_name}' of '{repo_full_name}': {exc}"
        ) from exc

    try:
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            content = json.loads(bundle.read(file_name))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise GitHubAppError(
            f"Artifact '{artifact_name}' of '{repo_full_name}' has no readable '{file_name}': {exc}"
        ) from exc
    return Artifact(artifact_id, created_at, content)


async def dispatch_workflow(
    installation_token: str,
    repo_full_name: str,
    workflow: str,
    ref: str,
    inputs: dict[str, str] | None = None,
) -> None:
    """
    Raises:
        GitHubAppError: On HTTP errors, including a workflow without a
            ``workflow_dispatch`` trigger (HTTP 422).
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/workflows/{workflow}/dispatches",
                headers=_api_headers(installation_token),
                json={"ref": ref, **({"inputs": inputs} if inputs else {})},
                timeout=15.0,
            )
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to start '{workflow}' on '{repo_full_name}': {exc}"
        ) from exc
    if response.status_code >= 400:
        raise GitHubAppError(
            f"Failed to start '{workflow}' on '{repo_full_name}': "
            f"{_github_error_message(response)}"
        )


async def list_workflow_runs(
    installation_token: str,
    repo_full_name: str,
    workflow: str,
    created_since: datetime,
) -> list[dict]:
    """
    The dispatched runs of *workflow* created since *created_since*, newest
    first, one page of 100.

    Raises:
        GitHubAppError: On HTTP errors.
    """
    if created_since.tzinfo is None:
        created_since = created_since.replace(tzinfo=timezone.utc)
    since = created_since.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/workflows/{workflow}/runs",
                headers=_api_headers(installation_token),
                params={
                    "event": "workflow_dispatch",
                    "created": f">={since}",
                    "per_page": 100,
                },
                timeout=15.0,
            )
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to list the runs of '{workflow}': {exc}"
        ) from exc
    if response.status_code >= 400:
        raise GitHubAppError(
            f"Failed to list the runs of '{workflow}': "
            f"{_github_error_message(response)}"
        )
    runs = response.json().get("workflow_runs")
    return runs if isinstance(runs, list) else []


async def get_workflow_run(
    installation_token: str, repo_full_name: str, workflow_run_id: str
) -> dict | None:
    """
    One workflow run, None when GitHub no longer has it.

    Raises:
        GitHubAppError: On other HTTP errors.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/runs/{workflow_run_id}",
                headers=_api_headers(installation_token),
                timeout=15.0,
            )
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to read workflow run {workflow_run_id}: {exc}"
        ) from exc
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise GitHubAppError(
            f"Failed to read workflow run {workflow_run_id}: "
            f"{_github_error_message(response)}"
        )
    return response.json()


class StaleBranchError(GitHubAppError):
    """The branch moved since it was read: re-read and recompute the change."""


async def get_branch_head(
    installation_token: str, repo_full_name: str, branch: str = "main"
) -> str:
    """The commit SHA a branch points at."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/git/ref/heads/{branch}",
                headers=_api_headers(installation_token),
                timeout=15.0,
            )
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to read {branch} of '{repo_full_name}': {exc}"
        ) from exc
    if response.status_code >= 400:
        raise GitHubAppError(
            f"Failed to read {branch} of '{repo_full_name}': "
            f"{_github_error_message(response)}"
        )
    return response.json()["object"]["sha"]


async def commit_files(
    installation_token: str,
    repo_full_name: str,
    files: dict[str, str | None],
    message: str,
    parent_sha: str,
    branch: str = "main",
) -> str:
    """
    Write several files in one commit on top of *parent_sha* (``None`` deletes).

    The Contents API makes one commit per file: a promotion of a frontend and
    a backend would reach Argo CD as two separate releases. The branch only
    moves if it still points at *parent_sha*.

    Raises:
        StaleBranchError: The branch moved since *parent_sha* was read.
        GitHubAppError: Any other GitHub failure.
    """
    headers = _api_headers(installation_token)
    base = f"{GITHUB_API_BASE}/repos/{repo_full_name}/git"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            parent = await client.get(
                f"{base}/commits/{parent_sha}", headers=headers
            )
            parent.raise_for_status()
            tree = [
                (
                    {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "content": content,
                    }
                    if content is not None
                    else {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": None,
                    }
                )
                for path, content in files.items()
            ]
            new_tree = await client.post(
                f"{base}/trees",
                headers=headers,
                json={"base_tree": parent.json()["tree"]["sha"], "tree": tree},
            )
            new_tree.raise_for_status()
            commit = await client.post(
                f"{base}/commits",
                headers=headers,
                json={
                    "message": message,
                    "tree": new_tree.json()["sha"],
                    "parents": [parent_sha],
                },
            )
            commit.raise_for_status()
            commit_sha = commit.json()["sha"]
            moved = await client.patch(
                f"{base}/refs/heads/{branch}",
                headers=headers,
                json={"sha": commit_sha, "force": False},
            )
    except httpx.HTTPStatusError as exc:
        raise GitHubAppError(
            f"Failed to commit to '{repo_full_name}': {_github_error_message(exc.response)}"
        ) from exc
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to commit to '{repo_full_name}': {exc}"
        ) from exc
    # Not a fast-forward of the current head.
    if moved.status_code == 422:
        raise StaleBranchError(
            f"{branch} of '{repo_full_name}' moved during the commit"
        )
    if moved.status_code >= 400:
        raise GitHubAppError(
            f"Failed to move {branch} of '{repo_full_name}': {_github_error_message(moved)}"
        )
    logger.info(
        "Committed %d file(s) to '%s' (%.8s)",
        len(files),
        repo_full_name,
        commit_sha,
    )
    return commit_sha


async def create_release(
    installation_token: str,
    repo_full_name: str,
    tag: str,
    target_sha: str,
    name: str,
    body: str,
) -> None:
    """
    Publish a GitHub Release on a new tag pointing at *target_sha*.

    Callers pick a tag the app's CI does not build (release/X.Y.Z, not
    vX.Y.Z): a tag pushed by the GitHub App does start workflows.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/releases",
                headers=_api_headers(installation_token),
                json={
                    "tag_name": tag,
                    "target_commitish": target_sha,
                    "name": name,
                    "body": body,
                },
                timeout=15.0,
            )
    except httpx.HTTPError as exc:
        raise GitHubAppError(
            f"Failed to publish release {tag}: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise GitHubAppError(
            f"Failed to publish release {tag} on '{repo_full_name}': "
            f"{_github_error_message(response)}"
        )
