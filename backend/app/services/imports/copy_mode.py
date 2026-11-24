"""
Copy mode of an import: the repository is mirrored into the platform org
and the import reads the copy, so the original keeps running untouched.

The CMP creates the empty private copy with the org installation, then the
first analysis run of the central workflow clones the original (bare: every
branch and tag, never refs/pull/*) and pushes it with ``git push --mirror``,
not GitHub's fork API: a fork of a private repository is deleted with it, or
when access to it is lost. The history comes over as it is, secrets
included, so the workflow scans it for leaks and reports them here.

"Pull changes from the original" is a sync run: the workflow pushes the
original's branch head to a cnp/sync-origin-<sha> branch of the copy and
the CMP opens a pull request, so nothing changed on the copy is overwritten.
The workflow's tokens are minted here, each down-scoped to one repository.
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.imports import ImportRun, ImportSource, RunKind, SourceMode
from app.services import audit
from app.services.app_repo import is_platform_repo_name
from app.services.github_service import (
    GitHubAppError,
    _call,
    create_repository,
    get_branch_head,
    get_org_installation_token,
    get_repository_id_scoped_token,
    open_pull_request,
)
from app.services.imports import service
from app.services.imports.access import RepoRef
from app.services.imports.service import ImportRefused

logger = logging.getLogger(__name__)

PENDING = "pending"
COPIED = "copied"
SYNC_PREFIX = "cnp/sync-origin-"
SYNC_BRANCH = re.compile(r"^cnp/sync-origin-[0-9a-f]{8}$")
MAX_NAME_ATTEMPTS = 20
MAX_LEAKS = 200
LEAK_FIELD_MAX = 300
ORIGIN_READ = {"contents": "read", "metadata": "read"}
# .github/workflows/* files need the workflows permission on top of contents.
COPY_PUSH = {"contents": "write", "workflows": "write", "metadata": "read"}
COPY_PULLS = {"pull_requests": "write", "contents": "read"}
PULL_TITLE = "chore(cnp): pull changes from {origin}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_copy(source: ImportSource) -> bool:
    return source.source_mode == SourceMode.COPY.value


def copy_name(project: str, repo_full_name: str, attempt: int = 0) -> str:
    """<project>-<repo>, then -2, -3... when GitHub says it is taken."""
    repo = repo_full_name.split("/", 1)[1]
    base = re.sub(r"[^a-z0-9._-]+", "-", f"{project}-{repo}".lower())
    base = base.strip("-.")[:90] or project
    return base if attempt == 0 else f"{base}-{attempt + 1}"


def sync_branch(sha: str) -> str:
    return f"{SYNC_PREFIX}{sha[:8]}"


async def _create_copy(project: str, origin: RepoRef) -> dict:
    token = await get_org_installation_token()
    for attempt in range(MAX_NAME_ATTEMPTS):
        name = copy_name(project, origin.full_name, attempt)
        if is_platform_repo_name(name):
            continue
        try:
            return await create_repository(
                token,
                name,
                org_name=settings.GITHUB_ORG,
                private=True,
                description=f"CNP copy of {origin.full_name}",
            )
        except GitHubAppError as exc:
            if "already exists" not in str(exc).lower():
                raise
    raise ImportRefused(
        f"No free repository name for a copy of {origin.full_name} in "
        f"{settings.GITHUB_ORG}",
        409,
    )


async def _origin_head(source: ImportSource) -> str:
    try:
        token = await get_repository_id_scoped_token(
            str(source.origin_installation_id),
            [source.origin_repo_id],
            ORIGIN_READ,
        )
        return await get_branch_head(
            token, source.origin_repo_full_name, source.branch
        )
    except GitHubAppError as exc:
        raise ImportRefused(
            f"Could not read branch {source.branch} of "
            f"{source.origin_repo_full_name}: {exc}",
            502,
        ) from exc


def _existing(db: Session, project: str, origin: RepoRef) -> ImportSource:
    return (
        db.query(ImportSource)
        .filter_by(
            project_name=project,
            origin_repo_id=origin.repo_id,
            source_mode=SourceMode.COPY.value,
        )
        .first()
    )


async def start_copy(  # pylint: disable=too-many-arguments
    db: Session,
    project: str,
    user: str,
    origin: RepoRef,
    branch: str | None,
    user_sub: str | None = None,
) -> ImportRun:
    """
    The analysis of a copy-mode import. The first one creates the copy and
    mirrors it; a later one analyses the copy, which may have moved on.
    """
    branch = branch or origin.default_branch
    if not service.BRANCH.fullmatch(branch) or ".." in branch:
        raise ImportRefused("Invalid branch name", 422)
    source = _existing(db, project, origin)
    if source is not None:
        service._refuse_if_busy(db, source)  # pylint: disable=W0212
        source.origin_installation_id = origin.installation_id
        source.branch = branch
        copied = (source.copy or {}).get("state") == COPIED
        sha = await (
            service.branch_head(source) if copied else _origin_head(source)
        )
    else:
        try:
            created = await _create_copy(project, origin)
        except GitHubAppError as exc:
            raise ImportRefused(
                f"Could not create the copy of {origin.full_name}: {exc}", 502
            ) from exc
        source = ImportSource(
            project_name=project,
            repo_full_name=created["full_name"],
            repo_id=int(created["id"]),
            installation_id=int(settings.GITHUB_INSTALLATION_ID),
            branch=branch,
            created_by=user,
            source_mode=SourceMode.COPY.value,
            origin_repo_full_name=origin.full_name,
            origin_repo_id=origin.repo_id,
            origin_installation_id=origin.installation_id,
            copy={"state": PENDING, "at": _now()},
        )
        db.add(source)
        service.set_rebuild_user(source, user, user_sub)
        audit.record_system_event(
            "import.copy.created",
            target=created["full_name"],
            details={"origin": origin.full_name},
            actor=user,
            project=project,
        )
        sha = await _origin_head(source)
    run = service._new_run(  # pylint: disable=W0212
        db, source, RunKind.ANALYZE, sha, user
    )
    await service._dispatch(db, run)  # pylint: disable=W0212
    return run


async def pull_origin(
    db: Session, source: ImportSource, user: str
) -> ImportRun:
    """A sync run: the original's branch head, as a pull request on the copy."""
    if not is_copy(source):
        raise ImportRefused("Only a copy-mode import has an original", 409)
    if (source.copy or {}).get("state") != COPIED:
        raise ImportRefused("The copy is not made yet", 409)
    service._refuse_if_busy(db, source)  # pylint: disable=W0212
    sha = await _origin_head(source)
    run = service._new_run(
        db, source, RunKind.SYNC, sha, user
    )  # pylint: disable=W0212
    await service._dispatch(db, run)  # pylint: disable=W0212
    return run


async def claim_copy(run: ImportRun) -> dict | None:
    """
    The ``copy`` object of the claim: what the workflow pushes where, with a
    read token on the original and a push token on the copy.
    """
    source = run.source
    if not is_copy(source):
        return None
    if run.kind == RunKind.SYNC.value:
        mode = "sync"
    elif (
        run.kind == RunKind.ANALYZE.value
        and (source.copy or {}).get("state") != COPIED
    ):
        mode = "mirror"
    else:
        return None
    origin_token = await get_repository_id_scoped_token(
        str(source.origin_installation_id),
        [source.origin_repo_id],
        ORIGIN_READ,
    )
    target_token = await get_repository_id_scoped_token(
        str(source.installation_id), [source.repo_id], COPY_PUSH
    )
    found = {
        "mode": mode,
        "origin": {
            "repository_id": source.origin_repo_id,
            "full_name": source.origin_repo_full_name,
            "branch": source.branch,
            "token": origin_token,
        },
        "target": {
            "repository_id": source.repo_id,
            "full_name": source.repo_full_name,
            "token": target_token,
        },
    }
    if mode == "sync":
        found["sync_branch"] = sync_branch(run.sha)
    return found


def _leak(item: dict) -> dict:
    """What is kept of a leak: where it is, never the value."""
    line = item.get("line")
    return {
        "rule": str(item.get("rule", ""))[:LEAK_FIELD_MAX],
        "file": str(item.get("file", ""))[:LEAK_FIELD_MAX],
        "commit": str(item.get("commit", ""))[:40],
        "line": line if isinstance(line, int) and line >= 0 else None,
    }


async def receive_copied(
    db: Session, run: ImportRun, leaks_total: int, leaks: list[dict]
) -> None:
    """The mirror is pushed and its history scanned; the analysis goes on."""
    source = run.source
    if run.kind != RunKind.ANALYZE.value or not is_copy(source):
        raise ImportRefused("This run does not copy a repository", 409)
    kept = [_leak(item) for item in leaks[:MAX_LEAKS]]
    source.copy = {
        "state": COPIED,
        "at": _now(),
        "sha": run.sha,
        "leaks_total": max(leaks_total, len(kept)),
        "leaks": kept,
    }
    db.commit()
    audit.record_system_event(
        "import.copy.mirrored",
        target=source.repo_full_name,
        details={
            "origin": source.origin_repo_full_name,
            "leaks": source.copy["leaks_total"],
        },
        actor="import-workflow",
        project=source.project_name,
    )
    try:
        # A mirror push leaves GitHub's default branch to chance.
        await _call(
            await get_org_installation_token(),
            "PATCH",
            f"/repos/{source.repo_full_name}",
            f"set the default branch of '{source.repo_full_name}'",
            json={"default_branch": source.branch},
        )
    except GitHubAppError as exc:
        logger.warning(
            "Default branch of %s not set: %s", source.repo_full_name, exc
        )


def _pull_body(source: ImportSource, sha: str) -> str:
    return (
        f"Changes of {source.origin_repo_full_name}@{source.branch} up to "
        f"{sha[:12]}, pulled by CNP into this copy.\n\n"
        "Merging applies them here; what changed on this side stays unless "
        "it conflicts. The original is never written."
    )


async def receive_synced(
    db: Session, run: ImportRun, branch: str, sha: str
) -> dict:
    """The original's head is on a branch of the copy: open its pull request."""
    source = run.source
    if run.kind != RunKind.SYNC.value:
        raise ImportRefused("Only a sync run pushes a sync branch", 409)
    if branch != sync_branch(run.sha) or sha != run.sha:
        raise ImportRefused("The sync branch is not the one claimed", 422)
    report: dict = {"branch": branch, "sha": sha}
    try:
        token = await get_repository_id_scoped_token(
            str(source.installation_id), [source.repo_id], COPY_PULLS
        )
        pull = await open_pull_request(
            token,
            source.repo_full_name,
            branch,
            source.branch,
            PULL_TITLE.format(origin=source.origin_repo_full_name),
            _pull_body(source, sha),
        )
        report["pull"] = pull["html_url"]
    except GitHubAppError as exc:
        if "no commits between" not in str(exc).lower():
            service.receive_failure(db, run, "publish", str(exc))
            raise ImportRefused(
                f"Could not open the pull request: {exc}", 502
            ) from exc
        report["up_to_date"] = True
    service.finish_sync(db, run, report)
    return report
