"""Starting, finishing and expiring verification runs."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.environment import AppEnvironment, VerificationRun
from app.models.project import TargetCloud
from app.services.environments import gitops, layout, observe, suites
from app.services.github_service import GitHubAppError, dispatch_workflow

logger = logging.getLogger(__name__)

ACTIVE = ("running",)
# A green run stays valid evidence for its digests this long.
FRESHNESS = timedelta(days=7)

_tasks: set[asyncio.Task] = set()


class RunRefused(Exception):
    """The run cannot start now; the message says why, for people."""


class DigestsMoved(RunRefused):
    """The environment no longer runs the digests the caller verifies."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(moment: datetime) -> datetime:
    """SQLite gives naive datetimes back; they were written as UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def public_url(
    db: Session, deployment: Deployment, environment: str
) -> str | None:
    if environment == "staging":
        row = db.get(AppEnvironment, (deployment.id, "staging"))
        return f"https://{row.hostname}/" if row and row.hostname else None
    try:
        hostname = json.loads(deployment.terraform_outputs or "{}").get(
            "hostname"
        )
    except ValueError:
        hostname = None
    return f"https://{hostname}/" if hostname else None


def active_run(
    db: Session, deployment_id: int, environment: str, suite: str
) -> VerificationRun | None:
    return (
        db.query(VerificationRun)
        .filter_by(
            deployment_id=deployment_id, environment=environment, suite=suite
        )
        .filter(VerificationRun.status.in_(ACTIVE))
        .first()
    )


def fresh_pass(
    db: Session,
    deployment_id: int,
    environment: str,
    suite: str,
    digests: dict,
) -> VerificationRun | None:
    """The latest run of *suite* that passed for exactly these digests, if recent."""
    candidates = (
        db.query(VerificationRun)
        .filter_by(
            deployment_id=deployment_id,
            environment=environment,
            suite=suite,
            status="passed",
        )
        .order_by(VerificationRun.started_at.desc())
        .limit(20)
        .all()
    )
    for run in candidates:
        if (
            run.digests == digests
            and now() - aware(run.started_at) < FRESHNESS
        ):
            return run
    return None


async def start(
    db: Session,
    deployment: Deployment,
    environment: str,
    spec: dict,
    actor: str,
    promotion_id: int | None = None,
    expected_digests: dict | None = None,
) -> VerificationRun:
    """
    Record a run for what the environment runs now, and carry it out in the
    background. External runs wait for the workflow's callback.

    Raises:
        RunRefused: Environment asleep (it is woken), moving, already being
            verified by this suite, or running other digests than expected.
    """
    state = await asyncio.to_thread(observe.of, deployment, environment)
    if state.asleep:
        await asyncio.to_thread(
            observe.wake,
            state.namespace,
            TargetCloud(gitops.target_cloud(deployment)),
        )
        raise RunRefused(
            f"{environment} was asleep (offhours): it is waking up, the run starts once it is up."
        )
    if not state.converged:
        raise RunRefused(
            f"{environment} is rolling out or not ready: try again once it is up."
        )
    if expected_digests is not None and state.digests != expected_digests:
        raise DigestsMoved(
            f"{environment} now runs another version than the one being promoted."
        )
    if active_run(db, deployment.id, environment, spec["name"]):
        raise RunRefused(
            f"Suite '{spec['name']}' is already running on {environment}."
        )

    started = now()
    run = VerificationRun(
        deployment_id=deployment.id,
        environment=environment,
        suite=spec["name"],
        kind=spec["kind"],
        digests=state.digests,
        version=state.version,
        status="running",
        promotion_id=promotion_id,
        actor=actor,
        started_at=started,
        deadline_at=started
        + timedelta(minutes=int(spec.get("timeoutMinutes") or 10)),
    )
    token = None
    if spec["kind"] == "external":
        token, run.token_hash = suites.new_token()
    db.add(run)
    db.commit()
    db.refresh(run)

    task = asyncio.create_task(_carry_out(run.id, spec, token))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run


async def _carry_out(run_id: int, spec: dict, token: str | None) -> None:
    with SessionLocal() as db:
        run = db.get(VerificationRun, run_id)
        deployment = db.get(Deployment, run.deployment_id)
        try:
            outcome = await _execute(db, deployment, run, spec, token)
        except Exception as exc:  # the run must end, whatever broke
            logger.exception("verification run %s failed to execute", run_id)
            outcome = suites.Outcome("error", f"Could not run: {exc}")
        if outcome is None:
            return
        db.refresh(run)
        if run.status == "running":
            finish(run, outcome)
            db.commit()


async def _execute(
    db: Session,
    deployment: Deployment,
    run: VerificationRun,
    spec: dict,
    token: str | None,
):
    kind = gitops.app_type(deployment)
    project, app = deployment.project_id, deployment.name
    namespace = layout.namespace(project, app, run.environment)
    cloud = gitops.target_cloud(deployment)
    if spec["kind"] == "smoke" and cloud != "onprem":
        target = public_url(db, deployment, run.environment)
        if not target:
            return suites.Outcome(
                "error", f"{run.environment} has no public hostname to test."
            )
        return await suites.run_public_smoke(target, project)
    if spec["kind"] == "smoke":
        gh = await gitops.token_for(deployment)
        base = await gitops.read_base(gh, gitops.repo_of(deployment), kind)
        releases = {
            c: layout.application(project, app, c, run.environment)
            for c in layout.components(kind)
        }
        paths = {c: gitops.smoke_path(v) for c, v in base.items()}
        return await suites.run_smoke(namespace, releases, paths)
    if spec["kind"] == "metrics":
        return await suites.run_metrics(
            namespace, suites.SuiteSpec.model_validate(spec)
        )
    if spec["kind"] == "external":
        target = public_url(db, deployment, run.environment)
        if not target:
            return suites.Outcome(
                "error", f"{run.environment} has no public hostname to test."
            )
        gh = await gitops.token_for(deployment)
        try:
            await dispatch_workflow(
                gh,
                gitops.repo_of(deployment),
                spec["workflow"],
                "main",
                {
                    "target_url": target,
                    "version": run.version or "unknown",
                    "callback_url": f"{settings.CMP_PUBLIC_URL}/api/verification-runs/{run.id}/result",
                    "callback_token": token or "",
                },
            )
        except GitHubAppError as exc:
            return suites.Outcome(
                "error", f"Could not start {spec['workflow']}: {exc}"
            )
        return None
    return suites.Outcome("error", f"Unknown suite kind {spec['kind']}")


def finish(run: VerificationRun, outcome: suites.Outcome) -> None:
    run.status = outcome.status
    run.summary = outcome.summary
    run.measurements = outcome.measurements
    run.finished_at = now()
    run.token_hash = None


def expire(db: Session) -> int:
    late = (
        db.query(VerificationRun)
        .filter(VerificationRun.status.in_(ACTIVE))
        .all()
    )
    count = 0
    for run in late:
        if aware(run.deadline_at) < now():
            finish(
                run, suites.Outcome("error", "No result before the timeout.")
            )
            count += 1
    if count:
        db.commit()
    return count
