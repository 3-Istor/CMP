"""
Repository import: runs, worker protocol and deployment (CNP-238, CNP-241).

A user binds a repository to a project and asks for an analysis; the central
workflow claims the run with its OIDC token, analyses the repository and
posts the plan. Once an admin approves the plan (with edits), a build run
builds and publishes the images, and the CMP writes the registry record and
values (one commit), the project's pull secret and generated credentials,
the DNS of public apps and one ``deployments`` row per app, then waits for
Argo CD.
"""

import asyncio
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.models.imports import (
    TERMINAL_STATUSES,
    ImportRun,
    ImportService,
    ImportSource,
    RunKind,
    RunStatus,
)
from app.services import audit
from app.services.github_service import (
    GitHubAppError,
    dispatch_workflow,
    get_branch_head,
    get_org_installation_token,
    get_repository_id_scoped_token,
)
from app.services.imports import failures, generate
from app.services.imports.access import RepoRef
from app.services.imports.failures import TOKEN_LIKE
from app.services.imports.generate import AppSpec, PlanError
from app.services.imports.plan_model import ImportPlan
from app.services.project_registry import RegistryError, list_records
from app.services.registry_names import check_app

logger = logging.getLogger(__name__)

BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,255}")
SAFE_PATH = re.compile(r"([A-Za-z0-9_.@+-]+(/[A-Za-z0-9_.@+-]+)*)?")
MAX_PLAN_BYTES = 2 * 1024 * 1024
MAX_MESSAGE = 2000
ARCHIVE_MAX_BYTES = 256 * 1024 * 1024
STAGES = frozenset({"claim", "fetch", "analyze", "build", "scan", "publish"})
SECRET_ENV = re.compile(r"PASSWORD|SECRET|TOKEN|_KEY$|CREDENTIAL", re.I)

_tasks: set[asyncio.Task] = set()
# Runs whose deploy runs in this process: never failed for being slow.
_deploying: set[str] = set()


class ImportRefused(Exception):
    """
    An import request refused, with the HTTP status to answer and, when
    another run is the reason, where to follow that run.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 409,
        latest_run: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.latest_run = latest_run
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_enabled() -> None:
    if not settings.IMPORT_ENABLED:
        raise ImportRefused("Repository import is not enabled", 404)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def redact(text: str) -> str:
    return TOKEN_LIKE.sub("[redacted]", text)[:MAX_MESSAGE]


def redacted_plan(plan: dict | None) -> dict | None:
    """The plan as users see it: no value of an env var that holds a secret."""
    if not plan:
        return plan
    shown = {**plan, "services": []}
    for service in plan.get("services") or []:
        env = [
            (
                {**e, "value": None}
                if e.get("secret") or SECRET_ENV.search(e.get("name", ""))
                else e
            )
            for e in service.get("env") or []
        ]
        shown["services"].append({**service, "env": env})
    return shown


def run_view(run: ImportRun) -> dict:
    plan = run.plan
    if run.kind == RunKind.BUILD.value and plan:
        plan = {"edits": plan.get("edits")}
    else:
        plan = redacted_plan(plan)
    report = run.report
    if report:
        report = {k: v for k, v in report.items() if k != "failure"}
        if isinstance(report.get("error"), str):
            report["error"] = failures.public_text(report["error"])
    return {
        "id": run.id,
        "source_id": run.source_id,
        "kind": run.kind,
        "sha": run.sha,
        "status": run.status,
        "plan": plan,
        "report": report,
        "failure": failures.failure_view(run),
        "requested_by": run.requested_by,
        "approved_by": run.approved_by,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def run_pointer(run: ImportRun) -> dict:
    """The run_id get_import takes, and the API and CMP pages of the run."""
    project = quote(run.source.project_name, safe="")
    return {
        "run_id": run.id,
        "kind": run.kind,
        "status": run.status,
        "api_path": f"/api/projects/{project}/imports/runs/{run.id}",
        "ui_path": f"/projects/{project}/import?run={run.id}",
    }


def service_view(service: ImportService) -> dict:
    return {
        "app": service.app_name,
        "kind": service.kind,
        "path": service.path,
        "status": service.status,
        "message": service.message,
        "last_sha": service.last_sha,
        "last_digest": service.last_digest,
        "deployment_id": service.deployment_id,
        "public": bool(service.spec.get("public")),
        "warnings": service.spec.get("warnings") or [],
    }


def source_view(source: ImportSource) -> dict:
    return {
        "id": source.id,
        "repo_full_name": source.repo_full_name,
        "branch": source.branch,
        "created_by": source.created_by,
        "created_at": source.created_at,
        "auto_rebuild": source.auto_rebuild,
        "rebuild_pending": source.rebuild_pending,
        "runs": [run_view(r) for r in source.runs[-5:]],
        "services": [service_view(s) for s in source.services],
    }


def get_source(db: Session, project: str, source_id: str) -> ImportSource:
    source = db.get(ImportSource, source_id)
    if source is None or source.project_name != project:
        raise ImportRefused("Import not found", 404)
    return source


def get_run(db: Session, project: str, run_id: str) -> ImportRun:
    run = db.get(ImportRun, run_id)
    if run is None or run.source.project_name != project:
        raise ImportRefused("Import run not found", 404)
    return run


def _timeouts() -> dict[str, int]:
    """Minutes without progress after which a run of each status fails."""
    return {
        RunStatus.QUEUED.value: settings.IMPORT_RUN_TIMEOUT_MINUTES,
        # No heartbeat between claim and publish: a build waiting for
        # runners can legitimately take hours.
        RunStatus.CLAIMED.value: settings.IMPORT_BUILD_TIMEOUT_MINUTES,
        RunStatus.RUNNING.value: settings.IMPORT_RUN_TIMEOUT_MINUTES,
    }


def expire_stale(
    source_id: str | None = None, project: str | None = None
) -> set[str]:
    """
    Fail the runs that hold an import's single active slot for nothing: a
    run without progress for its status's timeout (the workflow died, a
    deploy the CMP lost), or a queued or claimed run past its expiry. Only
    *source_id*'s or *project*'s runs when given. Its own session: the
    caller's pending changes are never committed with it. Returns the ids
    of the runs failed.
    """
    now = _now()
    waiting = [RunStatus.QUEUED.value, RunStatus.CLAIMED.value]
    stuck = [
        and_(
            ImportRun.status == status,
            ImportRun.updated_at < now - timedelta(minutes=minutes),
        )
        for status, minutes in _timeouts().items()
    ]
    expired: list[tuple[ImportRun, str]] = []
    with SessionLocal() as db:
        query = db.query(ImportRun).filter(
            or_(
                and_(
                    ImportRun.status.in_(waiting), ImportRun.expires_at < now
                ),
                *stuck,
            )
        )
        if source_id is not None:
            query = query.filter(ImportRun.source_id == source_id)
        if project is not None:
            query = query.join(ImportSource).filter(
                ImportSource.project_name == project
            )
        for run in query.all():
            if run.id in _deploying:
                continue
            timed_out = run.expires_at >= now
            reason = (
                f"Timed out: no progress for {_timeouts()[run.status]} "
                f"minutes while {run.status}"
                if timed_out
                else "expired"
            )
            result = db.execute(
                update(ImportRun)
                .where(ImportRun.id == run.id, ImportRun.status == run.status)
                .values(
                    status=RunStatus.FAILED.value,
                    updated_at=now,
                    report={
                        **(run.report or {}),
                        "stage": "timeout" if timed_out else "expired",
                        "error": reason,
                    },
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                expired.append((run, run.status))
        if not expired:
            return set()
        db.commit()
        for run, status in expired:
            audit.record_system_event(
                "import.expire",
                target=run.source.repo_full_name,
                details={"run": run.id, "kind": run.kind, "was": status},
                succeeded=False,
                project=run.source.project_name,
            )
    return {run.id for run, _ in expired}


def _active_run(source: ImportSource) -> ImportRun | None:
    return next(
        (
            r
            for r in source.runs
            if RunStatus(r.status) not in TERMINAL_STATUSES
        ),
        None,
    )


def expire_source(db: Session, source: ImportSource) -> None:
    """expire_stale for one import, seen at once through *db*."""
    expired = expire_stale(source.id)
    for run in source.runs:
        if run.id in expired:
            db.refresh(run)


def _refuse_if_busy(db: Session, source: ImportSource) -> None:
    expire_source(db, source)
    active = _active_run(source)
    if active is not None:
        raise ImportRefused(
            "A run of this import is already in progress",
            latest_run=run_pointer(active),
        )


def _last_run(source: ImportSource, kind: RunKind) -> ImportRun | None:
    runs = [r for r in source.runs if r.kind == kind.value]
    return runs[-1] if runs else None


async def _repo_token(source: ImportSource) -> str:
    """Contents and metadata, read only, on this one repository (D15)."""
    return await get_repository_id_scoped_token(
        str(source.installation_id),
        [source.repo_id],
        {"contents": "read", "metadata": "read"},
    )


async def _head(source: ImportSource) -> str:
    try:
        token = await _repo_token(source)
        return await get_branch_head(
            token, source.repo_full_name, source.branch
        )
    except GitHubAppError as exc:
        raise ImportRefused(
            f"Could not read branch {source.branch} of "
            f"{source.repo_full_name}: {exc}",
            502,
        ) from exc


async def branch_head(source: ImportSource) -> str:
    """The head of the import's branch, read with its scoped token."""
    return await _head(source)


def _new_run(
    db: Session,
    source: ImportSource,
    kind: RunKind,
    sha: str,
    user: str,
    plan: dict | None = None,
) -> ImportRun:
    run = ImportRun(
        source=source,
        kind=kind.value,
        sha=sha,
        status=RunStatus.QUEUED.value,
        requested_by=user,
        approved_by=user if kind is RunKind.BUILD else None,
        plan=plan,
        expires_at=_now() + timedelta(hours=settings.IMPORT_RUN_TTL_HOURS),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        # uq_import_runs_active_source: another request started a run
        # since this one checked.
        db.rollback()
        active = (
            db.query(ImportRun)
            .filter(
                ImportRun.source_id == source.id,
                ImportRun.status.notin_([s.value for s in TERMINAL_STATUSES]),
            )
            .first()
        )
        raise ImportRefused(
            "A run of this import is already in progress",
            latest_run=run_pointer(active) if active else None,
        ) from exc
    db.refresh(run)
    return run


async def _dispatch(db: Session, run: ImportRun) -> None:
    try:
        token = await get_org_installation_token()
        await dispatch_workflow(
            token,
            settings.IMPORT_WORKFLOW_REPO,
            settings.IMPORT_WORKFLOW_FILE,
            settings.IMPORT_WORKFLOW_REF.removeprefix("refs/heads/"),
            {"run_id": run.id, "attempt_id": run.attempt_id},
        )
    except GitHubAppError as exc:
        logger.warning("Import workflow dispatch of run %s: %s", run.id, exc)
        public = failures.public_text(redact(str(exc)))
        run.status = RunStatus.FAILED.value
        run.report = {"stage": "dispatch", "error": public}
        db.commit()
        raise ImportRefused(
            f"Could not start the import workflow: {public}", 502
        ) from exc


async def start_analysis(  # pylint: disable=too-many-arguments
    db: Session,
    project: str,
    user: str,
    repo: RepoRef,
    branch: str | None,
    user_sub: str | None = None,
) -> ImportRun:
    branch = branch or repo.default_branch
    if not BRANCH.fullmatch(branch) or ".." in branch:
        raise ImportRefused("Invalid branch name", 422)
    source = (
        db.query(ImportSource)
        .filter_by(project_name=project, repo_id=repo.repo_id)
        .first()
    )
    previous = None
    if source is None:
        source = ImportSource(
            project_name=project,
            repo_full_name=repo.full_name,
            repo_id=repo.repo_id,
            installation_id=repo.installation_id,
            branch=branch,
            created_by=user,
        )
        db.add(source)
        set_rebuild_user(source, user, user_sub)
    else:
        _refuse_if_busy(db, source)
        previous = (
            source.branch,
            source.repo_full_name,
            source.installation_id,
        )
    # The new branch is committed with the run that analyses it, by
    # _new_run, or not at all.
    source.branch = branch
    source.repo_full_name = repo.full_name
    source.installation_id = repo.installation_id
    try:
        sha = await _head(source)
    except ImportRefused:
        db.rollback()
        raise
    run = _new_run(db, source, RunKind.ANALYZE, sha, user)
    try:
        await _dispatch(db, run)
    except ImportRefused:
        if previous is not None:
            (
                source.branch,
                source.repo_full_name,
                source.installation_id,
            ) = previous
            db.commit()
        raise
    return run


def imported_app_names(db: Session, project: str) -> set[str]:
    """Names the project's imports hold, deployed yet or not."""
    return {
        name
        for (name,) in db.query(ImportService.app_name)
        .join(ImportSource)
        .filter(ImportSource.project_name == project)
        .all()
    }


async def _taken_names(
    db: Session, project: str, source: ImportSource
) -> dict[str, str]:
    """App names of the project held by something other than this import."""
    taken: dict[str, str] = {}
    for row in (
        db.query(Deployment)
        .filter(
            Deployment.project_id == project,
            Deployment.status != DeploymentStatus.DELETED,
        )
        .all()
    ):
        if not row.template_id.startswith("import-"):
            taken[row.name] = "an app of the project"
    for other in (
        db.query(ImportService)
        .join(ImportSource)
        .filter(
            ImportSource.project_name == project,
            ImportService.source_id != source.id,
        )
        .all()
    ):
        taken[other.app_name] = "another import of the project"
    return taken


def _other_imports_mib(db: Session, project: str, source: ImportSource) -> int:
    """Memory the project's other imports already request."""
    return sum(
        int(s.spec.get("requests_mem_mib") or 0)
        for s in db.query(ImportService)
        .join(ImportSource)
        .filter(
            ImportSource.project_name == project,
            ImportService.source_id != source.id,
        )
        .all()
    )


def capacity_view(db: Session, project: str, source: ImportSource) -> dict:
    """What check_capacity will hold the reviewed plan to, for the gauge."""
    return {
        "project_max_mib": settings.IMPORT_MAX_PROJECT_REQUESTS_MIB,
        "namespace_max_mib": settings.IMPORT_NAMESPACE_REQUESTS_MIB,
        "other_imports_mib": _other_imports_mib(db, project, source),
        # resolve() deploys catalogue backing apps with the preset, not with
        # what the analyzer guessed from the compose file.
        "backing_requests_mib": {
            kind: generate.preset_requests_mib(kind)
            for kind in generate.SUPPORTED_BACKING
        },
    }


async def approve(  # pylint: disable=too-many-arguments
    db: Session,
    project: str,
    user: str,
    source: ImportSource,
    edits: dict,
    run_id: str,
    user_sub: str | None = None,
) -> ImportRun:
    """
    Validate the edited plan and start the build. *run_id* is the analysis
    the person reviewed: a newer one, an assistant's re-analysis say, is
    never applied in its place.
    """
    _refuse_if_busy(db, source)
    analysis = _last_run(source, RunKind.ANALYZE)
    if analysis is None or analysis.status != RunStatus.SUCCEEDED.value:
        raise ImportRefused(
            "This import has no finished analysis to apply",
            latest_run=run_pointer(analysis) if analysis else None,
        )
    if analysis.id != run_id:
        raise ImportRefused(
            "The analysis changed since you reviewed it: review again",
            latest_run=run_pointer(analysis),
        )
    try:
        specs = generate.resolve(analysis.plan, edits)
    except PlanError as exc:
        raise ImportRefused("; ".join(exc.problems), 422) from exc
    if len(specs) > settings.IMPORT_MAX_APPS:
        raise ImportRefused(
            f"{len(specs)} apps, an import holds at most "
            f"{settings.IMPORT_MAX_APPS}",
            422,
        )

    problems: list[str] = []
    taken = await _taken_names(db, project, source)
    for spec in specs:
        if spec.name in taken:
            problems.append(f"'{spec.name}' is already {taken[spec.name]}")
    problems += generate.check_capacity(
        specs,
        settings.IMPORT_MAX_PROJECT_REQUESTS_MIB,
        settings.IMPORT_NAMESPACE_REQUESTS_MIB,
        settings.IMPORT_NAMESPACE_REQUESTS_MILLICPU,
        _other_imports_mib(db, project, source),
    )
    try:
        records = await list_records()
    except RegistryError as exc:
        raise ImportRefused(str(exc), 502) from exc
    for spec in specs:
        entry = {"name": spec.name, "type": spec.kind}
        if spec.public:
            entry["hostnames"] = {
                "prod": generate.hostname(project, spec.name)
            }
        problems += [
            f"app '{spec.name}': {p}"
            for p in check_app(project, entry, records)
        ]
    if problems:
        raise ImportRefused("; ".join(sorted(set(problems))), 422)

    # The admin who applies is who later rebuilds run as; stored with the
    # run, never by a refused request.
    set_rebuild_user(source, user, user_sub)
    # The run first: a request that loses the race to start it must not
    # leave its specs behind for the winner's build.
    run = _new_run(
        db,
        source,
        RunKind.BUILD,
        analysis.sha,
        user,
        plan={"plan": analysis.plan, "edits": edits},
    )
    _store_specs(db, source, specs)
    await _dispatch(db, run)
    return run


def _store_specs(
    db: Session, source: ImportSource, specs: list[AppSpec]
) -> None:
    current = {s.app_name: s for s in source.services}
    wanted = {spec.name for spec in specs}
    for name, row in current.items():
        # An app dropped from the plan before it was ever deployed goes; a
        # deployed one stays until the import is deleted.
        if (
            name not in wanted
            and row.last_digest is None
            and (row.kind == "service" or row.status == "pending")
        ):
            db.delete(row)
    for spec in specs:
        row = current.get(spec.name)
        if row is None:
            row = ImportService(source=source, app_name=spec.name)
            db.add(row)
        else:
            _keep_manual_links(spec, row.spec)
        row.kind = spec.kind
        row.path = spec.path
        row.spec = spec.to_dict()
        row.status = "pending" if row.last_digest is None else row.status
    db.commit()


def _keep_manual_links(spec: AppSpec, previous: dict) -> None:
    """Links drawn on the topology survive a new analysis of the repo."""
    aliases = {link["alias"] for link in spec.depends_on}
    for link in previous.get("depends_on") or []:
        if link.get("manual") and link["alias"] not in aliases:
            spec.depends_on.append(dict(link))
            aliases.add(link["alias"])


def set_rebuild_user(
    source: ImportSource, user: str, user_sub: str | None
) -> None:
    """Who automatic rebuilds run as from now on, once they proved it."""
    source.rebuild_user = user
    source.rebuild_user_sub = user_sub


def last_build(source: ImportSource) -> ImportRun | None:
    return _last_run(source, RunKind.BUILD)


async def rebuild(
    db: Session,
    user: str,
    source: ImportSource,
    apps: set[str] | None = None,
    sha: str | None = None,
    confirmed_by_sub: str | None = None,
) -> ImportRun:
    """
    Build the branch head, *sha* when the CMP resolved it already, never a
    commit a caller named. *apps* limits the build to those services; a
    service never built is always part of it. With *confirmed_by_sub*, the
    admin who confirmed it is who later rebuilds run as.
    """
    _refuse_if_busy(db, source)
    build = _last_run(source, RunKind.BUILD)
    if build is None or not source.services:
        raise ImportRefused("Apply the analysis before rebuilding")
    sha = sha or await _head(source)
    plan = {k: v for k, v in (build.plan or {}).items() if k != "apps"}
    if apps is not None:
        unbuilt = {
            s.app_name
            for s in source.services
            if s.kind == "service" and s.last_digest is None
        }
        plan["apps"] = sorted(apps | unbuilt)
    source.rebuild_pending = None
    if confirmed_by_sub is not None:
        set_rebuild_user(source, user, confirmed_by_sub)
    run = _new_run(db, source, RunKind.BUILD, sha, user, plan=plan)
    await _dispatch(db, run)
    return run


def built_apps(run: ImportRun) -> set[str]:
    """The services a build run builds."""
    services = {s.app_name for s in run.source.services if s.kind == "service"}
    only = (run.plan or {}).get("apps")
    return services if only is None else services & set(only)


# ── Worker protocol ─────────────────────────────────────────────────────────


def worker_run(
    db: Session, run_id: str, attempt_id: str, claims: dict
) -> ImportRun:
    """
    The run a worker call acts on, once the call is proven to come from
    the run's own workflow run and attempt.
    """
    run = db.get(ImportRun, run_id)
    if run is None or not hmac.compare_digest(
        run.attempt_id.encode(), str(attempt_id).encode()
    ):
        raise ImportRefused("Unknown run or attempt", 403)
    if run.workflow_run_id and (
        run.workflow_run_id != str(claims["run_id"])
        or run.workflow_run_attempt != str(claims["run_attempt"])
    ):
        raise ImportRefused(
            "This run was claimed by another workflow run or attempt", 403
        )
    if RunStatus(run.status) in TERMINAL_STATUSES:
        raise ImportRefused("This run is finished", 409)
    if run.expires_at < _now():
        _transition(
            db, run, run.status, RunStatus.FAILED, report={"error": "expired"}
        )
        raise ImportRefused("This run has expired", 410)
    return run


def _transition(
    db: Session,
    run: ImportRun,
    expected: str,
    status: RunStatus,
    **values,
) -> None:
    """Move *run* from *expected* to *status*, or refuse if it moved."""
    result = db.execute(
        update(ImportRun)
        .where(ImportRun.id == run.id, ImportRun.status == expected)
        .values(status=status.value, updated_at=_now(), **values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ImportRefused("The run changed meanwhile", 409)
    db.commit()
    db.refresh(run)


def _system_event(run: ImportRun, action: str, ok: bool, **details) -> None:
    audit.record_system_event(
        action,
        target=run.source.repo_full_name,
        details={"run": run.id, "kind": run.kind, "sha": run.sha, **details},
        actor="import-workflow",
        succeeded=ok,
        project=run.source.project_name,
    )


def _recipes(run: ImportRun) -> list[dict]:
    plan = run.plan["plan"]
    builds = {s["name"]: s["build"] for s in plan["services"]}
    wanted = built_apps(run)
    groups: dict[tuple, str] = {}
    recipes = []
    for service in sorted(run.source.services, key=lambda s: s.app_name):
        if service.app_name not in wanted:
            continue
        build = builds[service.spec["plan_name"]]
        for path in (build.get("context"), build.get("dockerfile")):
            if path is not None and (
                not SAFE_PATH.fullmatch(path) or ".." in path.split("/")
            ):
                raise ImportRefused(f"Unsafe path in the plan: {path!r}", 422)
        strategy = build["strategy"]
        key = (
            (build["context"], strategy)
            if strategy == "repaired_dockerfile"
            else (service.app_name,)
        )
        group = groups.setdefault(key, f"g{len(groups)}")
        recipes.append(
            {
                "app": service.app_name,
                "group": group,
                "context": build["context"],
                "strategy": strategy,
                "dockerfile": (
                    build.get("dockerfile")
                    if strategy == "dockerfile"
                    else None
                ),
                "dockerfile_text": (
                    build.get("generated_dockerfile")
                    if strategy == "repaired_dockerfile"
                    else None
                ),
                "dockerignore_text": build.get("generated_dockerignore"),
                "target": build.get("stage"),
                "railpack_env": build.get("railpack_env") or {},
                "image": image_name(run.source.project_name, service.app_name),
            }
        )
    return recipes


def image_name(project: str, app: str) -> str:
    return f"{settings.IMPORT_IMAGE_PREFIX}/{project}/{app}".lower()


async def claim(db: Session, run: ImportRun, claims: dict) -> dict:
    _transition(
        db,
        run,
        RunStatus.QUEUED.value,
        RunStatus.CLAIMED,
        workflow_run_id=str(claims["run_id"]),
        workflow_run_attempt=str(claims["run_attempt"]),
    )
    recipes = _recipes(run) if run.kind == RunKind.BUILD.value else []
    try:
        token = await _repo_token(run.source)
    except GitHubAppError as exc:
        _transition(
            db,
            run,
            RunStatus.CLAIMED.value,
            RunStatus.FAILED,
            report={"stage": "claim", "error": redact(str(exc))},
        )
        raise ImportRefused(
            "Could not mint the repository token", 502
        ) from exc
    _system_event(run, "import.claim", True, workflow_run=claims["run_id"])
    return {
        "run_id": run.id,
        "kind": run.kind,
        "project": run.source.project_name,
        "repo_full_name": run.source.repo_full_name,
        # The worker fetches /repositories/{id}/tarball/{sha}: a name can
        # point at another repository by the time it runs.
        "repository_id": run.source.repo_id,
        "sha": run.sha,
        "branch": run.source.branch,
        "token": token,
        "archive_max_bytes": ARCHIVE_MAX_BYTES,
        "recipes": recipes,
    }


def receive_plan(db: Session, run: ImportRun, plan: dict) -> None:
    if run.kind != RunKind.ANALYZE.value:
        raise ImportRefused("Only an analysis run posts a plan", 409)
    try:
        parsed = ImportPlan.model_validate(plan)
    except ValueError as exc:
        raise ImportRefused(f"Invalid plan: {exc}", 422) from exc
    if parsed.source.sha not in (None, run.sha):
        raise ImportRefused("The plan is for another commit", 422)
    names = [s.name for s in parsed.services] + [
        b.name for b in parsed.backing
    ]
    if len(names) != len(set(names)):
        raise ImportRefused("The plan names two apps the same", 422)
    if len(names) > settings.IMPORT_MAX_APPS:
        raise ImportRefused(
            f"The plan has {len(names)} apps, an import holds at most "
            f"{settings.IMPORT_MAX_APPS}",
            422,
        )
    stored = parsed.model_dump(by_alias=True, mode="json")
    for item in stored["services"]:
        item["env"], warnings = generate.scrub_env(item["env"])
        stored["warnings"] += [f"{item['name']}: {w}" for w in warnings]
    _transition(
        db,
        run,
        RunStatus.CLAIMED.value,
        RunStatus.SUCCEEDED,
        plan=stored,
    )
    _system_event(run, "import.analyze", True)


def receive_publish(db: Session, run: ImportRun, services: list[dict]) -> None:
    if run.kind != RunKind.BUILD.value:
        raise ImportRefused("Only a build run publishes images", 409)
    expected = built_apps(run)
    digests: dict[str, dict] = {}
    for item in services:
        app = item.get("app")
        if app not in expected or app in digests:
            raise ImportRefused(f"Unexpected app {app!r}", 422)
        if item.get("image") != image_name(run.source.project_name, app):
            raise ImportRefused(f"Image of {app} is not where it belongs", 422)
        if not generate.DIGEST.fullmatch(str(item.get("digest", ""))):
            raise ImportRefused(f"Digest of {app} is malformed", 422)
        digests[app] = {
            "digest": item["digest"],
            "vulns": item.get("vulns") or {},
            "sbom": item.get("sbom_summary") or {},
        }
    if set(digests) != expected:
        missing = ", ".join(sorted(expected - set(digests)))
        raise ImportRefused(f"Missing images: {missing}", 422)
    _transition(
        db,
        run,
        RunStatus.CLAIMED.value,
        RunStatus.RUNNING,
        report={"images": digests},
    )
    _system_event(run, "import.publish", True, apps=sorted(digests))
    # Imported here: deploy builds on this module.
    from app.services.imports.deploy import deploy

    _spawn(deploy(run.id))


def receive_failure(  # pylint: disable=too-many-arguments
    db: Session,
    run: ImportRun,
    stage: str,
    message: str,
    details: dict | None = None,
    workflow_run_id: str | None = None,
) -> None:
    """
    The first report wins: a later one finds the run finished (409). The
    workflow URL goes to the logs and the journal, never to users.
    """
    stage = stage if stage in STAGES else "unknown"
    kept = failures.clean_details(details, failures.known_apps(run))
    report = {
        **(run.report or {}),
        "stage": stage,
        "error": failures.public_text(redact(message)),
    }
    if kept:
        report["failure"] = kept
    url = failures.workflow_run_url(workflow_run_id or run.workflow_run_id)
    _transition(db, run, run.status, RunStatus.FAILED, report=report)
    logger.info(
        "Import run %s failed at %s (%s): %s",
        run.id,
        stage,
        url,
        redact(message),
    )
    _system_event(
        run,
        "import.fail",
        False,
        stage=stage,
        reason=kept.get("reason"),
        apps=kept.get("apps"),
        workflow_run=workflow_run_id or run.workflow_run_id,
    )
