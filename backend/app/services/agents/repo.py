"""
App repository operations for assistants: read the tree, commit several files
at once, open a pull request, tag a release.

Paths an assistant may write are checked here, server side. CI workflows,
the Dockerfile and deploy/ can run code with the repository's secrets or
change how the app is exposed, so they are "sensitive" and follow their own
line of the policy.
"""

import fnmatch
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.services.github_service import (
    GITHUB_API_BASE,
    GitHubAppError,
    _api_headers,
    _github_error_message,
)

MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FILES = 60

SENSITIVE_PATTERNS = (
    ".github/*",
    ".github/**",
    "Dockerfile",
    "*/Dockerfile",
    "Dockerfile.*",
    "*/Dockerfile.*",
    "deploy/*",
    "deploy/**",
    "*/deploy/*",
    "*/deploy/**",
    "docker-compose*.yml",
    "*.tf",
)
FORBIDDEN_PATTERNS = (
    ".env",
    "*/.env",
    ".env.*",
    "*/.env.*",
    "*.pem",
    "*.key",
    "*id_rsa*",
    ".git/*",
    ".git/**",
)
_ALLOWED_ENV_EXAMPLES = (".env.example", ".env.sample")

_SECRET_SHAPES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b[sr]k_(live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{40,}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),
)


class RepoError(Exception):
    pass


def normalize_path(path: str) -> str:
    clean = posixpath.normpath(path.strip().lstrip("/"))
    if clean in ("", ".") or clean.startswith("..") or "\\" in path:
        raise RepoError(f"Invalid path: {path!r}")
    return clean


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def classify(paths: list[str]) -> str:
    """'sensitive' when any path is, else 'code'. Raises on forbidden ones."""
    sensitive = False
    for path in paths:
        name = posixpath.basename(path)
        if name not in _ALLOWED_ENV_EXAMPLES and _matches(
            path, FORBIDDEN_PATTERNS
        ):
            raise RepoError(
                f"{path}: secrets and keys never go in the repository. Use "
                "request_secret to have the user enter the value in the CMP."
            )
        sensitive = sensitive or _matches(path, SENSITIVE_PATTERNS)
    return "sensitive" if sensitive else "code"


def find_secrets(files: dict[str, str]) -> list[str]:
    return [
        path
        for path, content in files.items()
        if any(shape.search(content) for shape in _SECRET_SHAPES)
    ]


def check_size(files: dict[str, str]) -> None:
    if len(files) > MAX_FILES:
        raise RepoError(f"At most {MAX_FILES} files per commit.")
    total = sum(len(c.encode()) for c in files.values())
    if total > MAX_TOTAL_BYTES:
        raise RepoError("At most 2 MB per commit.")


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    token: str,
    **kwargs,
) -> dict | list:
    response = await client.request(
        method, url, headers=_api_headers(token), timeout=30, **kwargs
    )
    if response.status_code >= 400:
        raise GitHubAppError(_github_error_message(response))
    return response.json() if response.content else {}


async def list_files(token: str, repo: str, ref: str = "main") -> list[str]:
    async with httpx.AsyncClient() as client:
        tree = await _request(
            client,
            "GET",
            f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{ref}",
            token,
            params={"recursive": "1"},
        )
    return sorted(
        item["path"] for item in tree.get("tree", []) if item["type"] == "blob"
    )


@dataclass
class CommitResult:
    sha: str
    branch: str
    pull_request_url: str | None = None


async def commit_files(
    token: str,
    repo: str,
    files: dict[str, str],
    message: str,
    *,
    base_branch: str = "main",
    as_pull_request: bool = False,
    pr_body: str = "",
) -> CommitResult:
    """
    One commit with every file, so the app is never left half-updated. With
    ``as_pull_request`` the commit lands on a new branch and a pull request
    is opened against ``base_branch``.
    """
    base_url = f"{GITHUB_API_BASE}/repos/{repo}"
    async with httpx.AsyncClient() as client:
        ref = await _request(
            client, "GET", f"{base_url}/git/ref/heads/{base_branch}", token
        )
        parent_sha = ref["object"]["sha"]
        parent = await _request(
            client, "GET", f"{base_url}/git/commits/{parent_sha}", token
        )
        tree = await _request(
            client,
            "POST",
            f"{base_url}/git/trees",
            token,
            json={
                "base_tree": parent["tree"]["sha"],
                "tree": [
                    {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "content": content,
                    }
                    for path, content in files.items()
                ],
            },
        )
        commit = await _request(
            client,
            "POST",
            f"{base_url}/git/commits",
            token,
            json={
                "message": message,
                "tree": tree["sha"],
                "parents": [parent_sha],
            },
        )
        if not as_pull_request:
            await _request(
                client,
                "PATCH",
                f"{base_url}/git/refs/heads/{base_branch}",
                token,
                json={"sha": commit["sha"]},
            )
            return CommitResult(sha=commit["sha"], branch=base_branch)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch = f"agent/{stamp}"
        await _request(
            client,
            "POST",
            f"{base_url}/git/refs",
            token,
            json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
        pull = await _request(
            client,
            "POST",
            f"{base_url}/pulls",
            token,
            json={
                "title": message.splitlines()[0][:120],
                "head": branch,
                "base": base_branch,
                "body": pr_body,
            },
        )
        return CommitResult(
            sha=commit["sha"],
            branch=branch,
            pull_request_url=pull.get("html_url"),
        )


_SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


async def latest_version(token: str, repo: str) -> str | None:
    async with httpx.AsyncClient() as client:
        tags = await _request(
            client,
            "GET",
            f"{GITHUB_API_BASE}/repos/{repo}/tags",
            token,
            params={"per_page": 100},
        )
    versions = [
        tuple(int(x) for x in m.groups())
        for t in tags
        if (m := _SEMVER.match(t["name"]))
    ]
    if not versions:
        return None
    major, minor, patch = max(versions)
    return f"v{major}.{minor}.{patch}"


def next_version(current: str | None, bump: str) -> str:
    if current is None:
        return "v0.1.0" if bump != "major" else "v1.0.0"
    major, minor, patch = (int(x) for x in _SEMVER.match(current).groups())
    if bump == "major":
        return f"v{major + 1}.0.0"
    if bump == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


async def create_tag(
    token: str, repo: str, tag: str, branch: str = "main"
) -> str:
    """A lightweight tag on the branch head; returns the tagged commit."""
    base_url = f"{GITHUB_API_BASE}/repos/{repo}"
    async with httpx.AsyncClient() as client:
        ref = await _request(
            client, "GET", f"{base_url}/git/ref/heads/{branch}", token
        )
        sha = ref["object"]["sha"]
        await _request(
            client,
            "POST",
            f"{base_url}/git/refs",
            token,
            json={"ref": f"refs/tags/{tag}", "sha": sha},
        )
    return sha
