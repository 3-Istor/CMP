"""
Runs whose workflow died without reporting (CNP-268).

A workflow run cancelled, out of runners or failed before its
report-failure job could post leaves the run queued or claimed until the
2 h timeout. The reconciler asks GitHub, with the CNP-Portal installation
token the CMP dispatches with, how the workflow run of each active run
ended, and fails the run a few minutes after its workflow completed.

Rate: nothing while no run is active; then the listing of the workflow's
runs since the oldest unclaimed run (the CMP knows them by their run-name,
"CNP import <run id>"), page by page up to MAX_PAGES, and one read per
claimed run, a few at a time. The installation token is reused until close
to its one-hour expiry.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.imports import ImportRun, RunStatus
from app.services import audit
from app.services.github_service import (
    LIST_PAGE_SIZE,
    GitHubAppError,
    get_org_installation_token,
    get_workflow_run,
    list_workflow_runs,
)
from app.services.imports import failures

logger = logging.getLogger(__name__)

RUN_NAME = re.compile(r"CNP import ([0-9a-f-]{36})")
# Time for the report-failure job to post after the last job ended.
REPORT_GRACE = timedelta(minutes=2)
# A dispatch GitHub never turned into a workflow run.
NOT_STARTED_AFTER = timedelta(minutes=10)
LISTING_MARGIN = timedelta(minutes=5)
WATCHED = (RunStatus.QUEUED.value, RunStatus.CLAIMED.value)
MAX_PAGES = 5
CONCURRENT_READS = 4
# Installation tokens live one hour.
TOKEN_REUSE_SECONDS = 50 * 60

_token: dict = {"value": None, "at": 0.0}


@dataclass(frozen=True)
class Watched:
    """What the watch needs of a run, read before GitHub is asked."""

    id: str
    status: str
    workflow_run_id: str | None
    created_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _github_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _by_run_name(listing: list[dict]) -> dict[str, dict]:
    """The newest workflow run of each CMP run id."""
    found: dict[str, dict] = {}
    for workflow_run in listing:
        match = RUN_NAME.fullmatch(str(workflow_run.get("display_title")))
        if not match:
            continue
        current = found.get(match.group(1))
        if current is None or workflow_run.get("id", 0) > current.get("id", 0):
            found[match.group(1)] = workflow_run
    return found


def verdict(
    run: Watched,
    workflow_run: dict | None,
    now: datetime,
    listing_complete: bool = True,
) -> tuple[str, str] | None:
    """
    (cause, message) when *run* is dead, None while it may still report. A
    run missing from an incomplete listing, or a claimed run GitHub no
    longer has, is left to the timeout.
    """
    if workflow_run is None:
        if (
            listing_complete
            and run.workflow_run_id is None
            and run.status == RunStatus.QUEUED.value
            and now - run.created_at > NOT_STARTED_AFTER
        ):
            return (
                "workflow_not_started",
                "The import workflow did not start",
            )
        return None
    if workflow_run.get("status") != "completed":
        return None
    ended = _github_time(workflow_run.get("updated_at"))
    if ended is None or now - ended < REPORT_GRACE:
        return None
    conclusion = str(workflow_run.get("conclusion") or "unknown")
    return (
        "workflow_stopped",
        f"The import workflow ended ({conclusion}) without reporting",
    )


async def _installation_token() -> str:
    if (
        _token["value"] is None
        or time.monotonic() - _token["at"] >= TOKEN_REUSE_SECONDS
    ):
        _token["value"] = await get_org_installation_token()
        _token["at"] = time.monotonic()
    return _token["value"]


async def _listing(token: str, since: datetime) -> tuple[list[dict], bool]:
    """The runs created since *since*, and whether the listing is whole."""
    runs: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        batch = await list_workflow_runs(
            token,
            settings.IMPORT_WORKFLOW_REPO,
            settings.IMPORT_WORKFLOW_FILE,
            since,
            page=page,
        )
        runs += batch
        if len(batch) < LIST_PAGE_SIZE:
            return runs, True
    return runs, False


async def _workflow_runs(
    runs: list[Watched],
) -> tuple[dict[str, dict | None], bool]:
    token = await _installation_token()
    found: dict[str, dict | None] = {}
    complete = True
    unclaimed = [r for r in runs if r.workflow_run_id is None]
    if unclaimed:
        since = min(r.created_at for r in unclaimed) - LISTING_MARGIN
        listing, complete = await _listing(token, since)
        named = _by_run_name(listing)
        for run in unclaimed:
            found[run.id] = named.get(run.id)
    gate = asyncio.Semaphore(CONCURRENT_READS)

    async def read(run: Watched) -> None:
        async with gate:
            found[run.id] = await get_workflow_run(
                token, settings.IMPORT_WORKFLOW_REPO, run.workflow_run_id
            )

    await asyncio.gather(*(read(r) for r in runs if r.workflow_run_id))
    return found, complete


def _fail(db, run: ImportRun, status: str, cause: str, message: str) -> bool:
    result = db.execute(
        update(ImportRun)
        .where(ImportRun.id == run.id, ImportRun.status == status)
        .values(
            status=RunStatus.FAILED.value,
            updated_at=_now(),
            report={
                **(run.report or {}),
                "stage": "workflow",
                "error": message,
                "failure": {"cause": cause},
            },
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def check_workflows() -> set[str]:
    """Fail the active runs whose workflow is over; returns their ids."""
    now = _now()
    # Read, then ask GitHub with no transaction open: SQLite would hold
    # every writer for as long as GitHub takes to answer.
    with SessionLocal() as db:
        runs = [
            Watched(r.id, r.status, r.workflow_run_id, r.created_at)
            for r in db.query(ImportRun)
            .filter(
                ImportRun.status.in_(WATCHED),
                ImportRun.updated_at < now - REPORT_GRACE,
            )
            .all()
        ]
    if not runs:
        return set()
    try:
        workflow_runs, complete = await _workflow_runs(runs)
    except GitHubAppError as exc:
        _token["value"] = None
        logger.warning("Import workflow watch skipped: %s", exc)
        return set()
    dead = {}
    for run in runs:
        found = verdict(run, workflow_runs.get(run.id), now, complete)
        if found is not None:
            dead[run.id] = (run, *found)
    if not dead:
        return set()
    failed = []
    with SessionLocal() as db:
        for row in db.query(ImportRun).filter(ImportRun.id.in_(dead)).all():
            watched, cause, message = dead[row.id]
            if _fail(db, row, watched.status, cause, message):
                failed.append((row, cause, workflow_runs.get(row.id)))
        db.commit()
        for run, cause, workflow_run in failed:
            url = (workflow_run or {}).get(
                "html_url"
            ) or failures.workflow_run_url(run.workflow_run_id)
            logger.warning(
                "Import run %s failed: workflow %s (%s)", run.id, cause, url
            )
            audit.record_system_event(
                "import.workflow_dead",
                target=run.source.repo_full_name,
                details={
                    "run": run.id,
                    "kind": run.kind,
                    "cause": cause,
                    "conclusion": (workflow_run or {}).get("conclusion"),
                    "workflow_run": (workflow_run or {}).get("id")
                    or run.workflow_run_id,
                },
                succeeded=False,
                project=run.source.project_name,
            )
    return {run.id for run, _, _ in failed}
