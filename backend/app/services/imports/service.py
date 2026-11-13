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

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
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
    get_scoped_installation_token,
)
from app.services.imports import generate
from app.services.imports.access import RepoRef
from app.services.imports.generate import AppSpec, PlanError
from app.services.imports.plan_model import ImportPlan
from app.services.project_registry import RegistryError, list_records
from app.services.registry_names import check_app

logger = logging.getLogger(__name__)

BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
SAFE_PATH = re.compile(r"^([A-Za-z0-9_.@+-]+(/[A-Za-z0-9_.@+-]+)*)?$")
MAX_PLAN_BYTES = 2 * 1024 * 1024
MAX_MESSAGE = 2000
ARCHIVE_MAX_BYTES = 256 * 1024 * 1024
STAGES = frozenset({"claim", "fetch", "analyze", "build", "scan", "publish"})
SECRET_ENV = re.compile(r"PASSWORD|SECRET|TOKEN|_KEY$|CREDENTIAL", re.I)
TOKEN_LIKE = re.compile(
    r"(gh[opsur]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"
    r"|[Bb]earer\s+\S+)"
)

_tasks: set[asyncio.Task] = set()


class ImportRefused(Exception):
    """An import request refused, with the HTTP status to answer."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        self.status_code = status_code
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
    return {
        "id": run.id,
        "source_id": run.source_id,
        "kind": run.kind,
        "sha": run.sha,
        "status": run.status,
        "plan": plan,
        "report": run.report,
        "requested_by": run.requested_by,
        "approved_by": run.approved_by,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
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


def expire_stale(db: Session) -> None:
    db.execute(
        update(ImportRun)
        .where(
            ImportRun.status.in_(
                [RunStatus.QUEUED.value, RunStatus.CLAIMED.value]
            ),
            ImportRun.expires_at < _now(),
        )
        .values(status=RunStatus.FAILED.value, report={"error": "expired"})
    )
    db.commit()


def _active_run(source: ImportSource) -> ImportRun | None:
    return next(
        (
            r
            for r in source.runs
            if RunStatus(r.status) not in TERMINAL_STATUSES
        ),
        None,
    )


def _last_run(source: ImportSource, kind: RunKind) -> ImportRun | None:
    runs = [r for r in source.runs if r.kind == kind.value]
    return runs[-1] if runs else None


async def _repo_token(source: ImportSource) -> str:
    """Contents and metadata, read only, on this one repository (D15)."""
    return await get_scoped_installation_token(
        str(source.installation_id),
        [source.repo_full_name.split("/", 1)[1]],
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
    db.commit()
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
        run.status = RunStatus.FAILED.value
        run.report = {"stage": "dispatch", "error": redact(str(exc))}
        db.commit()
        raise ImportRefused(
            f"Could not start the import workflow: {exc}", 502
        ) from exc


async def start_analysis(
    db: Session, project: str, user: str, repo: RepoRef, branch: str | None
) -> ImportRun:
    branch = branch or repo.default_branch
    if not BRANCH.match(branch) or ".." in branch:
        raise ImportRefused("Invalid branch name", 422)
    source = (
        db.query(ImportSource)
        .filter_by(project_name=project, repo_id=repo.repo_id)
        .first()
    )
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
    else:
        if _active_run(source) is not None:
            raise ImportRefused("A run of this import is already in progress")
        source.branch = branch
        source.repo_full_name = repo.full_name
        source.installation_id = repo.installation_id
    db.commit()
    sha = await _head(source)
    run = _new_run(db, source, RunKind.ANALYZE, sha, user)
    await _dispatch(db, run)
    return run


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


async def approve(
    db: Session,
    project: str,
    user: str,
    source: ImportSource,
    edits: dict,
) -> ImportRun:
    """Validate the edited plan and start the build."""
    if _active_run(source) is not None:
        raise ImportRefused("A run of this import is already in progress")
    analysis = _last_run(source, RunKind.ANALYZE)
    if analysis is None or analysis.status != RunStatus.SUCCEEDED.value:
        raise ImportRefused("This import has no finished analysis to apply")
    try:
        specs = generate.resolve(analysis.plan, edits)
    except PlanError as exc:
        raise ImportRefused("; ".join(exc.problems), 422) from exc

    problems: list[str] = []
    taken = await _taken_names(db, project, source)
    for spec in specs:
        if spec.name in taken:
            problems.append(f"'{spec.name}' is already {taken[spec.name]}")
    other_mib = sum(
        int(s.spec.get("requests_mem_mib") or 0)
        for s in db.query(ImportService)
        .join(ImportSource)
        .filter(
            ImportSource.project_name == project,
            ImportService.source_id != source.id,
        )
        .all()
    )
    problems += generate.check_capacity(
        specs,
        settings.IMPORT_MAX_PROJECT_REQUESTS_MIB,
        settings.IMPORT_NAMESPACE_REQUESTS_MIB,
        settings.IMPORT_NAMESPACE_REQUESTS_MILLICPU,
        other_mib,
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

    _store_specs(db, source, specs)
    run = _new_run(
        db,
        source,
        RunKind.BUILD,
        analysis.sha,
        user,
        plan={"plan": analysis.plan, "edits": edits},
    )
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
        row.kind = spec.kind
        row.path = spec.path
        row.spec = spec.to_dict()
        row.status = "pending" if row.last_digest is None else row.status
    db.commit()


async def rebuild(db: Session, user: str, source: ImportSource) -> ImportRun:
    if _active_run(source) is not None:
        raise ImportRefused("A run of this import is already in progress")
    build = _last_run(source, RunKind.BUILD)
    if build is None or not source.services:
        raise ImportRefused("Apply the analysis before rebuilding")
    sha = await _head(source)
    run = _new_run(db, source, RunKind.BUILD, sha, user, plan=build.plan)
    await _dispatch(db, run)
    return run


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
    if run.workflow_run_id and run.workflow_run_id != str(claims["run_id"]):
        raise ImportRefused(
            "This run was claimed by another workflow run", 403
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
    groups: dict[tuple, str] = {}
    recipes = []
    for service in sorted(run.source.services, key=lambda s: s.app_name):
        if service.kind != "service":
            continue
        build = builds[service.spec["plan_name"]]
        for path in (build.get("context"), build.get("dockerfile")):
            if path is not None and (
                not SAFE_PATH.match(path) or ".." in path.split("/")
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
    _transition(
        db,
        run,
        RunStatus.CLAIMED.value,
        RunStatus.SUCCEEDED,
        plan=parsed.model_dump(by_alias=True, mode="json"),
    )
    _system_event(run, "import.analyze", True)


def receive_publish(db: Session, run: ImportRun, services: list[dict]) -> None:
    if run.kind != RunKind.BUILD.value:
        raise ImportRefused("Only a build run publishes images", 409)
    expected = {s.app_name for s in run.source.services if s.kind == "service"}
    digests: dict[str, dict] = {}
    for item in services:
        app = item.get("app")
        if app not in expected or app in digests:
            raise ImportRefused(f"Unexpected app {app!r}", 422)
        if item.get("image") != image_name(run.source.project_name, app):
            raise ImportRefused(f"Image of {app} is not where it belongs", 422)
        if not generate.DIGEST.match(str(item.get("digest", ""))):
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


def receive_failure(
    db: Session, run: ImportRun, stage: str, message: str
) -> None:
    stage = stage if stage in STAGES else "unknown"
    _transition(
        db,
        run,
        run.status,
        RunStatus.FAILED,
        report={
            **(run.report or {}),
            "stage": stage,
            "error": redact(message),
        },
    )
    _system_event(run, "import.fail", False, stage=stage)
