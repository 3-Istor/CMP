"""
Automatic rebuilds of imports (CNP-244).

CNP-Portal sends GitHub ``push`` events, signed with the webhook secret. A
delivery only says that a branch moved and which paths the commits touched:
the commit to build is always the branch head the CMP reads itself with the
repository's scoped token. A reconciler compares that head with the last
build every few minutes, which catches the deliveries lost while the CMP
slept.

A rebuild runs as the import's rebuild user (its creator, then the last
admin who confirmed a rebuild) only while that user is still admin of the
project and can still import the repository. Otherwise the import waits,
``rebuild_pending``, until an admin confirms the rebuild with step-up.
"""

import asyncio
import hashlib
import hmac
import logging
import posixpath
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.roles import ProjectRole
from app.models.imports import (
    ImportRun,
    ImportSource,
    ImportWebhookDelivery,
    RunStatus,
)
from app.services import audit, keycloak_service, notifications
from app.services.authz import assert_project_role
from app.services.github_service import GITHUB_API_BASE, GitHubAppError
from app.services.imports import access, service
from app.services.imports.service import ImportRefused

logger = logging.getLogger(__name__)

DELIVERY = re.compile(r"[A-Za-z0-9-]{1,64}")
DELIVERY_TTL = timedelta(hours=24)
# GitHub lists at most 2048 commits in a push payload.
MAX_LISTED = 2048
# The compare API lists at most 300 files.
COMPARE_MAX_FILES = 300
COMPOSE = re.compile(r"(docker-)?compose(\.[\w-]+)*\.ya?ml")
# Build files that, outside every service folder, are shared by the services
# below them: a root pom.xml or package.json changes them all.
SHARED_MANIFESTS = frozenset(
    {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "yarn.lock",
        "go.mod",
        "go.sum",
        "go.work",
        "Cargo.toml",
        "Cargo.lock",
        "pyproject.toml",
        "poetry.lock",
        "requirements.txt",
        ".dockerignore",
    }
)
REFUSAL_JOURNAL_SECONDS = 60.0

_last_refusal = {"at": 0.0}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Webhook ─────────────────────────────────────────────────────────────────


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256 over the raw body, compared in constant time."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(
        expected.encode(), header.removeprefix("sha256=").encode()
    )


def note_refused(reason: str, remote: str | None) -> None:
    """
    Log every refused delivery; journal at most one a minute, so an
    unauthenticated caller cannot fill the journal.
    """
    logger.warning("GitHub webhook refused (%s) from %s", reason, remote)
    now = time.monotonic()
    if now - _last_refusal["at"] < REFUSAL_JOURNAL_SECONDS:
        return
    _last_refusal["at"] = now
    audit.record_system_event(
        "import.webhook.refused",
        target="github",
        details={"reason": reason},
        actor="github-webhook",
        succeeded=False,
    )


def remember_delivery(db: Session, delivery_id: str) -> bool:
    """False when this delivery was already received in the last 24 h."""
    db.execute(
        delete(ImportWebhookDelivery).where(
            ImportWebhookDelivery.received_at < _now() - DELIVERY_TTL
        )
    )
    db.add(ImportWebhookDelivery(id=delivery_id, received_at=_now()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def changed_paths(payload: dict) -> set[str] | None:
    """
    The paths the pushed commits touched, or None when the payload does not
    tell them all: everything is rebuilt then.
    """
    commits = payload.get("commits")
    if (
        payload.get("forced")
        or not isinstance(commits, list)
        or not commits
        or len(commits) >= MAX_LISTED
    ):
        return None
    paths: set[str] = set()
    for commit in commits:
        if not isinstance(commit, dict):
            return None
        for key in ("added", "modified", "removed"):
            listed = commit.get(key)
            if not isinstance(listed, list):
                return None
            for path in listed:
                if not isinstance(path, str):
                    return None
                paths.add(path)
    if len(paths) >= MAX_LISTED:
        return None
    return paths


def _clean(path: str | None) -> str | None:
    if path is None:
        return None
    path = posixpath.normpath(path.strip("/")) if path.strip("/") else ""
    return "" if path == "." else path


def _under(path: str, prefix: str) -> bool:
    return prefix == "" or path == prefix or path.startswith(prefix + "/")


def _targets(source: ImportSource, build: ImportRun) -> list[tuple]:
    """(app, folder, build context, dockerfile) of each built service."""
    plan = (build.plan or {}).get("plan") or {}
    builds = {
        s.get("name"): s.get("build") or {} for s in plan.get("services") or []
    }
    targets = []
    for row in source.services:
        if row.kind != "service":
            continue
        recipe = builds.get(row.spec.get("plan_name"), {})
        targets.append(
            (
                row.app_name,
                _clean(row.path) or "",
                _clean(recipe.get("context")),
                _clean(recipe.get("dockerfile")),
            )
        )
    return targets


def affected_apps(
    source: ImportSource, build: ImportRun, changed: set[str]
) -> set[str] | None:
    """
    The services to rebuild for *changed*, None for all of them:

    - a compose file anywhere: all;
    - a path in a service's folder, or its Dockerfile: that service;
    - else a path in a build context shared with other folders (a Maven
      reactor, its parent pom): the services built from that context;
    - else a shared build file above service folders: all;
    - anything else (docs, CI of the repository): nothing.
    """
    targets = _targets(source, build)
    hit: set[str] = set()
    for raw in changed:
        path = _clean(raw) or ""
        name = posixpath.basename(path)
        if COMPOSE.fullmatch(name):
            return None
        owners = {
            app
            for app, folder, _, dockerfile in targets
            if _under(path, folder) or path == dockerfile
        }
        if not owners:
            owners = {
                app
                for app, _, context, _ in targets
                if context is not None and _under(path, context)
            }
        if owners:
            hit |= owners
            continue
        directory = posixpath.dirname(path)
        if name in SHARED_MANIFESTS and any(
            _under(folder, directory) for _, folder, _, _ in targets
        ):
            return None
    return hit


def on_push(db: Session, payload: dict, delivery: str) -> list[dict]:
    """
    Start the rebuilds a push asks for, in the background. Sources are
    matched by installation and repository id, never by name.
    """
    installation = (payload.get("installation") or {}).get("id")
    repository = (payload.get("repository") or {}).get("id")
    ref = payload.get("ref")
    if (
        not _is_id(installation)
        or not _is_id(repository)
        or not isinstance(ref, str)
        or not ref.startswith("refs/heads/")
        or payload.get("deleted")
    ):
        return []
    branch = ref.removeprefix("refs/heads/")
    sources = (
        db.query(ImportSource)
        .filter_by(installation_id=installation, repo_id=repository)
        .all()
    )
    changed = changed_paths(payload)
    started = []
    for source in sources:
        if source.branch != branch or not source.auto_rebuild:
            continue
        build = service.last_build(source)
        if build is None:
            continue
        apps = (
            None if changed is None else affected_apps(source, build, changed)
        )
        if apps is not None and not apps:
            continue
        audit.record_system_event(
            "import.webhook.push",
            target=source.repo_full_name,
            details={
                "delivery": delivery,
                "apps": sorted(apps) if apps is not None else "all",
            },
            actor="github-webhook",
            project=source.project_name,
        )
        service._spawn(  # pylint: disable=protected-access
            auto_rebuild(source.id, apps, "push")
        )
        started.append({"source": source.id, "apps": apps})
    return started


# ── Rebuild on behalf of the rebuild user ───────────────────────────────────


class _Unavailable(Exception):
    """Keycloak or GitHub could not answer: try again later."""


def rebuild_identity(source: ImportSource) -> dict:
    claims = {"preferred_username": source.rebuild_user or source.created_by}
    if source.rebuild_user_sub:
        claims["sub"] = source.rebuild_user_sub
    return claims


async def _lost_rights(db: Session, source: ImportSource) -> str | None:
    """
    Why the rebuild user may no longer rebuild (inactive, not_admin,
    repo_access), None when they still may.
    Only what the CMP can prove without their token counts: a user who
    could import the repository through a platform group only (no GitHub
    link) cannot be checked here, and an admin confirms instead.
    """
    claims = rebuild_identity(source)
    try:
        if "sub" in claims:
            account = await run_in_threadpool(
                keycloak_service.get_user_by_id, claims["sub"]
            )
            if not account or not account.get("enabled", True):
                return "inactive"
        await run_in_threadpool(
            assert_project_role,
            db,
            claims,
            source.project_name,
            ProjectRole.ADMIN,
        )
    except HTTPException as exc:
        if exc.status_code in (401, 403, 404):
            return "not_admin"
        raise _Unavailable(str(exc.detail)) from exc
    try:
        repo = await access.resolve_repository(
            db, claims, source.repo_full_name, repo_id=source.repo_id
        )
    except access.RepoAccessError:
        return "repo_access"
    except GitHubAppError as exc:
        raise _Unavailable(str(exc)) from exc
    if repo.installation_id != source.installation_id:
        source.installation_id = repo.installation_id
        db.commit()
    return None


def _merge_apps(current: list | None, apps: set[str] | None) -> list | None:
    if current is None or apps is None:
        return None
    return sorted(set(current) | apps)


def _mark_pending(
    db: Session,
    source: ImportSource,
    reason: str,
    trigger: str,
    apps: set[str] | None,
) -> None:
    first = source.rebuild_pending is None
    source.rebuild_pending = {
        "reason": reason,
        "user": rebuild_identity(source)["preferred_username"],
        "trigger": trigger,
        "since": (
            source.rebuild_pending["since"]
            if not first
            else _now().isoformat()
        ),
        "apps": (
            _merge_apps(source.rebuild_pending.get("apps"), apps)
            if not first
            else (sorted(apps) if apps is not None else None)
        ),
    }
    db.commit()
    if not first:
        return
    audit.record_system_event(
        "import.rebuild.pending",
        target=source.repo_full_name,
        details={
            "reason": reason,
            "user": source.rebuild_pending["user"],
            "trigger": trigger,
        },
        actor="import-rebuild",
        succeeded=False,
        project=source.project_name,
    )
    notifications.notify_project(
        source.project_name,
        kind="approval_request",
        title_key="import_rebuild_pending",
        params={"repo": source.repo_full_name},
        link=f"/projects/{source.project_name}",
        minimum=ProjectRole.ADMIN,
    )


async def auto_rebuild(  # pylint: disable=too-many-return-statements
    source_id: str, apps: set[str] | None, trigger: str
) -> str:
    """
    Rebuild *apps* (None: all) of an import as its rebuild user. Returns
    what happened: started, pending, busy, deferred, skipped or failed.
    """
    with SessionLocal() as db:
        source = db.get(ImportSource, source_id)
        if source is None or not source.auto_rebuild:
            return "skipped"
        if source.rebuild_pending is not None:
            _mark_pending(
                db, source, source.rebuild_pending["reason"], trigger, apps
            )
            return "pending"
        if service._active_run(source) is not None:  # pylint: disable=W0212
            # The reconciler builds the head once this run is over.
            return "busy"
        try:
            reason = await _lost_rights(db, source)
        except _Unavailable as exc:
            logger.warning(
                "Rebuild of %s deferred: %s", source.repo_full_name, exc
            )
            return "deferred"
        if reason is not None:
            _mark_pending(db, source, reason, trigger, apps)
            return "pending"
        user = rebuild_identity(source)["preferred_username"]
        try:
            run = await service.rebuild(db, user, source, apps)
        except ImportRefused as exc:
            logger.warning(
                "Rebuild of %s refused: %s", source.repo_full_name, exc
            )
            return "failed"
        audit.record_system_event(
            "import.rebuild.auto",
            target=source.repo_full_name,
            details={
                "run": run.id,
                "sha": run.sha,
                "trigger": trigger,
                "as": user,
                "apps": (run.plan or {}).get("apps") or "all",
            },
            actor="import-rebuild",
            project=source.project_name,
        )
        return "started"


# ── Reconciler ──────────────────────────────────────────────────────────────


async def _compare(
    source: ImportSource, base: str, head: str
) -> set[str] | None:
    """Paths changed between two commits, None when GitHub cannot say."""
    try:
        token = await service._repo_token(source)  # pylint: disable=W0212
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repositories/{source.repo_id}/compare/"
                f"{base}...{head}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params={"per_page": 1},
            )
    except (GitHubAppError, httpx.HTTPError) as exc:
        logger.warning("Compare on %s failed: %s", source.repo_full_name, exc)
        return None
    if response.status_code != 200:
        return None
    body = response.json()
    files = body.get("files")
    if (
        body.get("status") not in ("ahead", "identical")
        or not isinstance(files, list)
        or len(files) >= COMPARE_MAX_FILES
    ):
        return None
    paths: set[str] = set()
    for item in files:
        for key in ("filename", "previous_filename"):
            if isinstance(item.get(key), str):
                paths.add(item[key])
    return paths


async def reconcile_source(source_id: str) -> str:
    with SessionLocal() as db:
        source = db.get(ImportSource, source_id)
        if (
            source is None
            or not source.auto_rebuild
            or source.rebuild_pending is not None
            or service._active_run(source) is not None  # pylint: disable=W0212
        ):
            return "skipped"
        build = service.last_build(source)
        if build is None:
            return "skipped"
        try:
            head = await service.branch_head(source)
        except ImportRefused as exc:
            logger.warning("Reconcile of %s: %s", source.repo_full_name, exc)
            return "deferred"
        if head in (build.sha, source.head_checked):
            return "up_to_date"
        changed = await _compare(source, build.sha, head)
        apps = (
            None if changed is None else affected_apps(source, build, changed)
        )
        if apps is not None and not apps:
            source.head_checked = head
            db.commit()
            return "up_to_date"
    return await auto_rebuild(source_id, apps, "reconcile")


async def reconcile_once() -> None:
    with SessionLocal() as db:
        ids = [
            s.id
            for s in db.query(ImportSource)
            .filter(ImportSource.auto_rebuild.is_(True))
            .all()
            if any(r.status == RunStatus.SUCCEEDED.value for r in s.runs)
        ]
    for source_id in ids:
        try:
            await reconcile_source(source_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Reconcile of import %s failed", source_id)


async def reconcile_loop() -> None:
    """First pass at startup: the CMP wakes up after a night asleep."""
    while True:
        try:
            await reconcile_once()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Import reconcile failed")
        await asyncio.sleep(settings.IMPORT_RECONCILE_INTERVAL_SECONDS)
