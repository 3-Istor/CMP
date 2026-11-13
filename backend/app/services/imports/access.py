"""
Which repositories a user may import (spec A).

Two ways in, both proven server-side, never from what the browser sends:

- the user linked GitHub (OAuth): their own installations of CNP-Portal and
  the 3-Istor org installation, limited to repositories GitHub says the user
  can push to;
- a platform admin (Keycloak group infra) may import any repository of the
  org installation, so the first imports work before linking ships.

Platform repositories are never importable (IMPORT_DENY_REPOS), by name
and by the id each name had when the CMP resolved it: a platform repository
renamed, or a fork given its old name, is still refused.
"""

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services.authz import is_cnp_admin
from app.services.github_service import (
    GITHUB_API_BASE,
    GitHubAppError,
    get_org_installation_token,
)

MAX_PAGES = 10
DENY_IDS_TTL_SECONDS = 3600

_deny_cache: dict[str, object] = {}
_deny_lock = asyncio.Lock()


@dataclass(frozen=True)
class RepoRef:
    full_name: str
    repo_id: int
    installation_id: int
    default_branch: str
    private: bool

    def public(self) -> dict:
        return {
            "full_name": self.full_name,
            "default_branch": self.default_branch,
            "private": self.private,
        }


class RepoAccessError(Exception):
    """The user may not import this repository."""


def denied_repos() -> frozenset[str]:
    return frozenset(
        name.strip().lower()
        for name in settings.IMPORT_DENY_REPOS.split(",")
        if name.strip()
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _resolve_ids(names: frozenset[str]) -> frozenset[int]:
    token = await get_org_installation_token()
    ids: set[int] = set()
    async with httpx.AsyncClient(timeout=20.0) as client:
        for name in sorted(names):
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{name}", headers=_headers(token)
            )
            # Gone, or out of the installation: nobody can import it either.
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                raise GitHubAppError(
                    f"GitHub /repos/{name}: HTTP {response.status_code}"
                )
            ids.add(int(response.json()["id"]))
    return frozenset(ids)


async def denied_repo_ids() -> frozenset[int]:
    """
    Raises:
        GitHubAppError: The ids could not be resolved; imports are refused
            until they are.
    """
    async with _deny_lock:
        names = denied_repos()
        if (
            _deny_cache.get("names") != names
            or time.monotonic() - float(_deny_cache.get("at", 0.0))
            > DENY_IDS_TTL_SECONDS
        ):
            _deny_cache.update(
                ids=await _resolve_ids(names), names=names, at=time.monotonic()
            )
        return _deny_cache["ids"]


def is_denied(repo: RepoRef, denied_ids: frozenset[int]) -> bool:
    return (
        repo.repo_id in denied_ids or repo.full_name.lower() in denied_repos()
    )


async def _paged(token: str, path: str, field: str) -> list[dict]:
    items: list[dict] = []
    headers = _headers(token)
    async with httpx.AsyncClient(timeout=20.0) as client:
        for page in range(1, MAX_PAGES + 1):
            response = await client.get(
                f"{GITHUB_API_BASE}{path}",
                headers=headers,
                params={"per_page": 100, "page": page},
            )
            if response.status_code >= 400:
                raise GitHubAppError(
                    f"GitHub {path}: HTTP {response.status_code}"
                )
            batch = response.json().get(field) or []
            items.extend(batch)
            if len(batch) < 100:
                break
    return items


def _ref(repo: dict, installation_id: int) -> RepoRef:
    return RepoRef(
        full_name=repo["full_name"],
        repo_id=int(repo["id"]),
        installation_id=installation_id,
        default_branch=repo.get("default_branch") or "main",
        private=bool(repo.get("private")),
    )


async def org_repositories() -> list[RepoRef]:
    """Repositories of the org installation, minus the platform's own."""
    denied = await denied_repo_ids()
    token = await get_org_installation_token()
    repos = await _paged(token, "/installation/repositories", "repositories")
    installation_id = int(settings.GITHUB_INSTALLATION_ID)
    refs = [
        _ref(repo, installation_id)
        for repo in repos
        if not repo.get("archived")
    ]
    return [ref for ref in refs if not is_denied(ref, denied)]


async def linked_repositories(db, claims: dict) -> list[RepoRef] | None:
    """
    Repositories proven through the user's GitHub link, or None when the
    user has not linked GitHub.
    """
    # Imported here: github_account imports the models of this package.
    from app.services import github_account

    return await github_account.importable_repositories(db, claims)


async def importable_repositories(db, claims: dict) -> list[RepoRef]:
    denied = await denied_repo_ids()
    linked = await linked_repositories(db, claims)
    found: dict[int, RepoRef] = {}
    for repo in linked or []:
        if not is_denied(repo, denied):
            found[repo.repo_id] = repo
    if is_cnp_admin(claims):
        for repo in await org_repositories():
            found.setdefault(repo.repo_id, repo)
    return sorted(found.values(), key=lambda r: r.full_name.lower())


async def resolve_repository(
    db, claims: dict, full_name: str, repo_id: int | None = None
) -> RepoRef:
    """
    The repository *full_name* as the user can import it now; with *repo_id*,
    only if the name still designates that repository.

    Raises:
        RepoAccessError: Deny-listed, or not among the user's repositories.
        GitHubAppError: GitHub, or the deny-list ids, could not be read.
    """
    if full_name.lower() in denied_repos():
        raise RepoAccessError(
            f"{full_name} is a platform repository and cannot be imported"
        )
    for repo in await importable_repositories(db, claims):
        if repo_id is not None and repo.repo_id == repo_id:
            return repo
        if repo_id is None and repo.full_name.lower() == full_name.lower():
            return repo
    raise RepoAccessError(
        f"{full_name} is not among the repositories you can import: link "
        "GitHub from your account page and install CNP-Portal on it"
    )
