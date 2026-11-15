"""
One Git Data API commit per change to cnp-projects (D2).

The record and the values files land together, so Argo CD never sees an app
without its values. Writers of the same repository queue on one lock in this
process; a commit that lost the race to another writer (the branch moved) is
recomputed on the new head and retried.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ruamel.yaml import YAML

from app.core.config import settings
from app.services.github_service import (
    FileNotInRepoError,
    GitHubAppError,
    StaleBranchError,
    commit_files,
    get_branch_head,
    get_file_content,
    get_org_installation_token,
)
from app.services.imports.generate import dump_yaml
from app.services.project_registry import RegistryError, record_path

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
_locks: dict[str, asyncio.Lock] = {}

# Gets the record (mutated in place) and the head commit it was read at, and
# returns the other files to write. Raising aborts the commit.
Change = Callable[[dict, str], Awaitable[dict[str, str | None]]]


def _lock(repo: str) -> asyncio.Lock:
    return _locks.setdefault(repo, asyncio.Lock())


def _retryable(exc: GitHubAppError) -> bool:
    return isinstance(exc, StaleBranchError) or "HTTP 409" in str(exc)


async def commit_record_change(
    project: str, change: Change, message: str
) -> str:
    """
    Read the project's record at the branch head, apply *change*, commit the
    record and the returned files in one commit, and return its SHA.

    Raises:
        RegistryError: No record, or GitHub kept refusing the commit.
    """
    repo = settings.CNP_REGISTRY_REPO
    branch = settings.CNP_REGISTRY_BRANCH
    path = record_path(project)
    async with _lock(repo):
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                token = await get_org_installation_token()
                head = await get_branch_head(token, repo, branch)
                try:
                    raw, _ = await get_file_content(
                        token, repo, path, ref=head
                    )
                except FileNotInRepoError as exc:
                    raise RegistryError(
                        f"Project '{project}' has no registry record."
                    ) from exc
                record = YAML().load(raw)
                if not isinstance(record, dict):
                    raise RegistryError(
                        f"Registry record of '{project}' is not a mapping"
                    )
                files = await change(record, head)
                files[path] = dump_yaml(record)
                return await commit_files(
                    token,
                    repo,
                    files,
                    f"{message} [skip ci]",
                    parent_sha=head,
                    branch=branch,
                )
            except GitHubAppError as exc:
                if not _retryable(exc):
                    raise RegistryError(
                        f"Could not commit to the registry: {exc}"
                    ) from exc
                last_error = exc
                logger.info(
                    "Registry commit for '%s' lost a race (attempt %d): %s",
                    project,
                    attempt,
                    exc,
                )
                await asyncio.sleep(0.5 * attempt)
        raise RegistryError(
            f"The registry kept moving, gave up after {MAX_ATTEMPTS} "
            f"attempts: {last_error}"
        )
