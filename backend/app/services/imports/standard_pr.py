"""
The standard pull request of an import (CNP-303) and the switch it leads to.

The CMP opens it with a token down-scoped to the one repository, never
pushes to the imported branch itself, and waits for a person to merge it.
Once merged, the import's apps read deploy/values-<app>.yaml from the
repository (repoURL in the record), their values leave cnp-projects, and the
repository's CI builds the images on vX.Y.Z tags: the platform's rebuild
stops, so only one of the two ever builds an app. Releases land on the
cnp/deploy branch, which the CMP creates and the image updater writes: the
CMP writes the default branch only through the pull request.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.imports import ImportSource, RunStatus
from app.services import audit, topology_cache
from app.services.github_service import (
    GitHubAppError,
    commit_files,
    create_branch,
    get_branch_head,
    get_file_content,
    get_org_installation_token,
    get_pull_request,
    get_repository_id_scoped_token,
    list_tree_paths,
    open_pull_request,
)
from app.services.imports import deploy, generate, service, standard_repo
from app.services.imports.generate import AppSpec
from app.services.imports.registry_commit import commit_record_change
from app.services.imports.service import ImportRefused
from app.services.project_registry import RegistryError

logger = logging.getLogger(__name__)

BRANCH_PREFIX = "cnp/standard-deploy"
TITLE = "chore(cnp): deploy this repository like a CNP template app"
COMMIT_MESSAGE = "chore(cnp): add deploy/ values and the CNP template CI"
# .github/workflows/ files need the workflows permission on top of contents.
WRITE = {"contents": "write", "pull_requests": "write", "workflows": "write"}
READ = {"contents": "read", "pull_requests": "read"}

OPEN = "open"
MERGED = "merged"
SWITCHED = "switched"
CLOSED = "closed"
FAILED = "failed"
# A new pull request may be opened only from these (or none yet).
REOPENABLE = frozenset({CLOSED, FAILED})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_enabled() -> None:
    if not settings.IMPORT_STANDARD_PR_ENABLED:
        raise ImportRefused("The standard pull request is not enabled", 404)


def ineligible(source: ImportSource) -> str | None:
    """Why the repository cannot follow the template model, if it cannot."""
    owner = source.repo_full_name.split("/", 1)[0]
    if owner.lower() != settings.GITHUB_ORG.lower():
        return (
            f"{source.repo_full_name} is not a repository of "
            f"{settings.GITHUB_ORG}: its CI could not publish to the "
            "project's registry nor sign as a CNP workflow"
        )
    return None


async def _token(source: ImportSource, permissions: dict[str, str]) -> str:
    return await get_repository_id_scoped_token(
        str(source.installation_id), [source.repo_id], permissions
    )


async def _template_files() -> dict[str, str]:
    token = await get_org_installation_token()
    files = {}
    for path, (repo, source_path) in standard_repo.TEMPLATE_FILES.items():
        content, _ = await get_file_content(
            token, f"{settings.GITHUB_ORG}/{repo}", source_path, ref="main"
        )
        files[path] = (
            standard_repo.on_import_runners(content)
            if path.startswith(standard_repo.WORKFLOWS_DIR)
            else content
        )
    return files


def _deployed(source: ImportSource) -> tuple[list[AppSpec], dict[str, str]]:
    """The apps the import runs, and the last digest of each service."""
    specs, digests = [], {}
    for row in source.services:
        if row.kind == "service" and row.last_digest:
            digests[row.app_name] = row.last_digest
        elif not (row.kind == "backing" and row.deployment_id):
            continue
        specs.append(AppSpec.from_dict(row.spec))
    return specs, digests


def _files(db: Session, source: ImportSource) -> tuple[dict, dict]:
    """The files of the pull request, and what the body says about them."""
    build = service.last_build(source)
    if build is None or build.status != RunStatus.SUCCEEDED.value:
        raise ImportRefused(
            "Deploy the import before opening its standard pull request"
        )
    specs, digests = _deployed(source)
    if not specs:
        raise ImportRefused("The import runs no app yet")
    project = source.project_name
    matrix, dockerfiles, excluded = standard_repo.builds(
        project, specs, (build.plan or {}).get("plan") or {}
    )
    wake_order = {
        spec.name: number
        for number, wave in enumerate(deploy.deploy_waves(specs))
        for spec in wave
    }
    values = standard_repo.repo_values(
        project,
        specs,
        digests,
        {b.app for b in matrix},
        source.branch,
        deploy._target_cloud(db, project),  # pylint: disable=W0212
        wake_order,
    )
    files = {
        **values,
        **dockerfiles,
        standard_repo.CI_PATH: standard_repo.ci_workflow(matrix),
    }
    return files, {
        "values": sorted(values),
        "matrix": matrix,
        "excluded": excluded,
    }


async def preview(db: Session, source: ImportSource) -> dict:
    """What the pull request would add and the conflicts it would show."""
    files, about = _files(db, source)
    paths = set(files) | set(standard_repo.TEMPLATE_FILES)
    token = await _token(source, {"contents": "read"})
    head = await get_branch_head(token, source.repo_full_name, source.branch)
    existing = await list_tree_paths(token, source.repo_full_name, head)
    return {
        "eligible": ineligible(source) is None,
        "reason": ineligible(source),
        "files": sorted(paths),
        "conflicts": standard_repo.conflicts(existing, paths),
        "excluded": about["excluded"],
    }


async def open_pr(db: Session, source: ImportSource, user: str) -> dict:
    """
    Raises:
        ImportRefused: Not eligible, already open or merged, a run in
            progress, nothing deployed yet, or GitHub refused a step.
    """
    ensure_enabled()
    reason = ineligible(source)
    if reason:
        raise ImportRefused(reason, 422)
    state = (source.standard_repo or {}).get("state")
    if state is not None and state not in REOPENABLE:
        raise ImportRefused(
            f"The standard pull request of {source.repo_full_name} is "
            f"already {state}",
            409,
        )
    service._refuse_if_busy(db, source)  # pylint: disable=W0212
    files, about = _files(db, source)
    repo = source.repo_full_name
    try:
        files.update(await _template_files())
        token = await _token(source, WRITE)
        head = await get_branch_head(token, repo, source.branch)
        found = standard_repo.conflicts(
            await list_tree_paths(token, repo, head), set(files)
        )
        branch = f"{BRANCH_PREFIX}-{head[:8]}"
        await create_branch(token, repo, branch, head)
        await commit_files(
            token, repo, files, COMMIT_MESSAGE, parent_sha=head, branch=branch
        )
        pull = await open_pull_request(
            token,
            repo,
            branch,
            source.branch,
            TITLE,
            standard_repo.pr_body(
                source.project_name,
                source.branch,
                about["values"],
                about["matrix"],
                found,
                about["excluded"],
            ),
        )
    except GitHubAppError as exc:
        source.standard_repo = {
            "state": FAILED,
            "error": service.redact(str(exc)),
            "at": _now(),
        }
        db.commit()
        raise ImportRefused(
            f"Could not open the standard pull request: {exc}", 502
        ) from exc
    source.standard_repo = {
        "state": OPEN,
        "number": int(pull["number"]),
        "url": pull["html_url"],
        "branch": branch,
        "base_sha": head,
        "opened_by": user,
        "opened_at": _now(),
        "apps": sorted(
            path.removeprefix("deploy/values-").removesuffix(".yaml")
            for path in about["values"]
        ),
        "built": sorted(b.app for b in about["matrix"]),
        "conflicts": found,
        "excluded": about["excluded"],
    }
    db.commit()
    audit.record_system_event(
        "import.standard_pr.opened",
        target=repo,
        details={"pull": pull["html_url"], "conflicts": len(found)},
        actor=user,
        project=source.project_name,
    )
    return source.standard_repo


async def open_after_deploy(source_id: str, user: str) -> None:
    """The import opens its pull request once its first deploy succeeded."""
    if not settings.IMPORT_STANDARD_PR_ENABLED:
        return
    with SessionLocal() as db:
        source = db.get(ImportSource, source_id)
        if (
            source is None
            or source.standard_repo is not None
            or ineligible(source)
        ):
            return
        try:
            await open_pr(db, source, user)
        except ImportRefused as exc:
            logger.warning(
                "Standard pull request of %s: %s", source.repo_full_name, exc
            )


async def refresh(source_id: str) -> str:
    """
    Follow the pull request: merged, the import switches to its repository;
    closed unmerged, it stays on the platform. Returns the state reached.
    """
    if not settings.IMPORT_STANDARD_PR_ENABLED:
        return "disabled"
    with SessionLocal() as db:
        source = db.get(ImportSource, source_id)
        record = dict((source.standard_repo or {}) if source else {})
        if record.get("state") not in (OPEN, MERGED):
            return record.get("state") or "none"
        if record["state"] == OPEN:
            try:
                token = await _token(source, READ)
                pull = await get_pull_request(
                    token, source.repo_full_name, record["number"]
                )
            except GitHubAppError as exc:
                logger.warning(
                    "Standard pull request of %s unreadable: %s",
                    source.repo_full_name,
                    exc,
                )
                return OPEN
            if pull.get("merged"):
                record.update(
                    state=MERGED,
                    merge_sha=pull.get("merge_commit_sha"),
                    merged_by=(pull.get("merged_by") or {}).get("login"),
                    merged_at=pull.get("merged_at"),
                )
            elif pull.get("state") == "closed":
                record.update(state=CLOSED, closed_at=pull.get("closed_at"))
            source.standard_repo = record
            db.commit()
            if record["state"] != MERGED:
                return record["state"]
        return await _switch(db, source)


async def _ensure_deploy_branch(
    source: ImportSource, record: dict, apps: set[str]
) -> None:
    """
    The branch the image updater writes releases to, made from the merge
    with the image each CI-built service runs until its first release. A
    file already there (a release, or an earlier attempt) is kept.

    Raises:
        GitHubAppError: The branch could not be read, made or written.
    """
    repo = source.repo_full_name
    branch = standard_repo.DEPLOY_BRANCH
    token = await _token(source, {"contents": "write"})
    try:
        head = await get_branch_head(token, repo, branch)
    except GitHubAppError:
        head = record["merge_sha"]
        await create_branch(token, repo, branch, head)
    digests = {
        row.app_name: row.last_digest
        for row in source.services
        if row.kind == "service" and row.last_digest
    }
    present = await list_tree_paths(token, repo, head)
    files = {
        path: text
        for path, text in standard_repo.image_files(
            source.project_name,
            set(record.get("built") or []) & apps,
            digests,
        ).items()
        if path not in present
    }
    if files:
        await commit_files(
            token,
            repo,
            files,
            "chore(cnp): pin the imported images until the first release",
            parent_sha=head,
            branch=branch,
        )


async def _switch(db: Session, source: ImportSource) -> str:
    """
    Point the apps whose values the merge brought at the repository. An app
    whose deploy/values-<app>.yaml was dropped before the merge stays on the
    platform's copy.
    """
    if service._active_run(source) is not None:  # pylint: disable=W0212
        return MERGED
    record = dict(source.standard_repo)
    repo = source.repo_full_name
    project = source.project_name
    try:
        token = await _token(source, {"contents": "read"})
        present = await list_tree_paths(token, repo, record["merge_sha"])
    except GitHubAppError as exc:
        logger.warning("Switch of %s deferred: %s", repo, exc)
        return MERGED
    apps = {
        app
        for app in record.get("apps") or []
        if standard_repo.values_file(app) in present
    }
    if not apps:
        record.update(
            state=FAILED,
            error="The merged pull request has no deploy/values-<app>.yaml "
            "left: the apps stay on the platform",
            at=_now(),
        )
        source.standard_repo = record
        db.commit()
        return FAILED
    try:
        await _ensure_deploy_branch(source, record, apps)
    except GitHubAppError as exc:
        logger.warning("Switch of %s deferred: %s", repo, exc)
        return MERGED
    switched: set[str] = set()

    async def change(registry: dict, head: str) -> dict[str, str | None]:
        del head
        switched.clear()
        switched.update(generate.switch_to_repo(registry, repo, apps))
        return {generate.values_path(project, app): None for app in switched}

    try:
        commit = await commit_record_change(
            project,
            change,
            f"feat(registry): deploy {repo} from its deploy/ folder "
            f"in {project}",
        )
    except RegistryError as exc:
        logger.warning("Switch of %s deferred: %s", repo, exc)
        return MERGED
    record.update(
        state=SWITCHED,
        switched_at=_now(),
        switched=sorted(switched),
        kept=sorted(set(record.get("apps") or []) - apps),
        commit=commit,
    )
    source.standard_repo = record
    source.auto_rebuild = False
    source.rebuild_pending = None
    db.commit()
    topology_cache.invalidate(project)
    audit.record_system_event(
        "import.standard_pr.switched",
        target=repo,
        details={"apps": sorted(switched), "commit": commit},
        actor="import-standard-pr",
        project=project,
    )
    return SWITCHED


async def refresh_all() -> None:
    """The reconciler's pass over the pull requests not settled yet."""
    if not settings.IMPORT_STANDARD_PR_ENABLED:
        return
    with SessionLocal() as db:
        ids = [
            s.id
            for s in db.query(ImportSource).all()
            if (s.standard_repo or {}).get("state") in (OPEN, MERGED)
        ]
    for source_id in ids:
        try:
            await refresh(source_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Standard pull request of %s failed", source_id)
