"""An import failure users can understand and act on (CNP-268)."""

import asyncio
from datetime import timedelta

import pytest

from app.core.config import settings
from app.models.imports import ImportRun, ImportService
from app.services.imports import failures, rebuilds, service, watch
from tests.test_import_api import (  # noqa: F401
    PLAN,
    _call,
    _run,
    _source,
    client,
    world,
)

ACTIONS_URL = (
    "https://github.com/3-Istor/extern-project-analyzer/actions/runs/123456"
)
NETTY = {
    "app": "user-service",
    "id": "CVE-2026-75595",
    "package": "io.netty:netty-handler",
    "installed": "4.1.90.Final",
    "fixed": "4.1.137.Final",
    "severity": "CRITICAL",
}
PARSSON = {
    "app": "user-service",
    "id": "CVE-2023-7272",
    "package": "org.eclipse.parsson:parsson",
    "installed": "1.1.1",
    "fixed": "1.1.3",
    "severity": "CRITICAL",
}


@pytest.fixture
def journal(monkeypatch):
    events = []
    monkeypatch.setattr(
        service.audit,
        "record_system_event",
        lambda action, **kwargs: events.append((action, kwargs)),
    )
    return events


def _build(
    world, status="claimed", apps=("user-service", "post-service"), **fields
):
    """A build run of an import of *apps*, analysed on its branch."""
    source = _source(world, auto_rebuild=True)
    _run(world, source, status="succeeded", plan=PLAN)
    with world.db() as db:
        for app in apps:
            db.add(
                ImportService(
                    source_id=source.id,
                    app_name=app,
                    kind="service",
                    spec={"plan_name": app},
                )
            )
        db.commit()
    return _run(
        world,
        source,
        kind="build",
        status=status,
        plan={"plan": PLAN, "edits": {}},
        **fields,
    )


def _detail(client, run):
    response = client.get(f"/api/projects/shop/imports/runs/{run.id}")
    assert response.status_code == 200
    return response.json()


def _scan_block(client, run, **details):
    return _call(
        client,
        run,
        "fail",
        stage="scan",
        message="blocked by fixable critical vulnerabilities: user-service "
        "(2 critical with a fix)",
        details={
            "reason": "scan_blocked",
            "apps": ["user-service"],
            "vulnerabilities": [NETTY, PARSSON],
            "log_excerpt": "user-service: {'critical': 2}",
            **details,
        },
    )


# ── Details from the worker ─────────────────────────────────────────────────


def test_a_scan_block_shows_each_package_to_upgrade(client, world):
    run = _build(world)

    _scan_block(client, run)

    assert _detail(client, run)["failure"]["upgrades"] == [
        {
            "package": "io.netty:netty-handler",
            "installed": "4.1.90.Final",
            "fixed": "4.1.137.Final",
            "apps": ["user-service"],
            "ids": ["CVE-2026-75595"],
        },
        {
            "package": "org.eclipse.parsson:parsson",
            "installed": "1.1.1",
            "fixed": "1.1.3",
            "apps": ["user-service"],
            "ids": ["CVE-2023-7272"],
        },
    ]


def test_a_scan_block_offers_to_fix_the_repo_then_rebuild(client, world):
    run = _build(world)

    _scan_block(client, run)

    failure = _detail(client, run)["failure"]
    assert (failure["cause"], failure["actions"], failure["apps"]) == (
        "scan_blocked",
        ["fix_in_repo", "retry"],
        ["user-service"],
    )


def test_a_scan_block_says_a_push_rebuilds_on_its_own(client, world):
    run = _build(world)

    _scan_block(client, run)

    assert _detail(client, run)["failure"]["rebuilds_on_push"] is True


def test_a_report_without_details_is_still_accepted(client, world):
    run = _build(world)

    response = _call(
        client,
        run,
        "fail",
        stage="build",
        message=f"The build job did not succeed, see {ACTIONS_URL}",
    )

    assert (
        response.status_code,
        _detail(client, run)["failure"]["cause"],
    ) == (
        204,
        "build_failed",
    )


def test_the_workflow_url_never_reaches_users(client, world):
    run = _build(world)

    _call(
        client,
        run,
        "fail",
        stage="build",
        message=f"The build job did not succeed, see {ACTIONS_URL}",
        details={"log_excerpt": f"logs at {ACTIONS_URL}/job/9"},
    )

    shown = str(_detail(client, run)) + str(
        client.get("/api/projects/shop/imports").json()
    )
    assert "extern-project-analyzer" not in shown


def test_the_workflow_url_is_kept_in_the_journal(client, world, journal):
    run = _build(world)

    _scan_block(client, run)

    (event,) = [k for a, k in journal if a == "import.fail"]
    assert event["details"]["workflow_run_url"] == (
        "https://github.com/3-Istor/extern-project-analyzer/actions/runs/555"
    )


def test_a_url_stored_before_the_change_is_hidden_too(client, world):
    run = _build(
        world,
        status="failed",
    )
    with world.db() as db:
        db.get(ImportRun, run.id).report = {
            "stage": "build",
            "error": f"The build job did not succeed, see {ACTIONS_URL}",
        }
        db.commit()

    assert _detail(client, run)["report"]["error"] == (
        "The build job did not succeed"
    )


def test_a_later_generic_report_keeps_the_detailed_one(client, world):
    run = _build(world)
    _scan_block(client, run)

    second = _call(
        client, run, "fail", stage="build", message="The build job failed"
    )

    failure = _detail(client, run)["failure"]
    assert (second.status_code, failure["cause"], failure["stage"]) == (
        409,
        "scan_blocked",
        "scan",
    )


def test_malformed_details_are_dropped_and_the_failure_kept(client, world):
    run = _build(world)

    response = _call(
        client,
        run,
        "fail",
        stage="scan",
        message="blocked",
        details={
            "reason": "rm -rf",
            "apps": ["user-service", "not-an-app", 3],
            "vulnerabilities": [
                {**NETTY, "app": "elsewhere"},
                {**NETTY, "fixed": None},
                "x",
                PARSSON,
            ],
            "log_excerpt": ["not", "text"],
        },
    )

    with world.db() as db:
        kept = db.get(ImportRun, run.id).report["failure"]
    assert (response.status_code, kept) == (
        204,
        {"apps": ["user-service"], "vulnerabilities": [PARSSON]},
    )


def test_the_log_excerpt_is_redacted_and_capped(client, world):
    run = _build(world)

    _call(
        client,
        run,
        "fail",
        stage="build",
        message="api: docker buildx exited with code 1",
        details={"log_excerpt": "ghs_" + "a" * 30 + "\n" + "x" * 20000},
    )

    excerpt = _detail(client, run)["failure"]["log_excerpt"]
    assert ("ghs_" in excerpt, len(excerpt)) == (False, 8000)


# ── Causes and actions ──────────────────────────────────────────────────────


def _failed(world, kind="build", **report):
    run = (
        _build(world, status="failed")
        if kind == "build"
        else _run(world, _source(world), status="failed")
    )
    with world.db() as db:
        db.get(ImportRun, run.id).report = report
        db.commit()
    return run


@pytest.mark.parametrize(
    "kind,report,actions",
    [
        ("build", {"stage": "timeout", "error": "x"}, ["retry"]),
        ("build", {"error": "expired"}, ["retry"]),
        (
            "build",
            {"stage": "build", "failure": {"reason": "plan_mismatch"}},
            ["reanalyze"],
        ),
        (
            "build",
            {"stage": "workflow", "failure": {"cause": "workflow_stopped"}},
            ["retry"],
        ),
        ("build", {"stage": "publish"}, ["retry", "contact_platform"]),
        ("build", {"stage": "deploy"}, ["retry", "contact_platform"]),
        ("build", {"stage": "fetch"}, ["retry", "reanalyze"]),
        ("analyze", {"stage": "analyze"}, ["retry", "contact_platform"]),
        ("analyze", {"stage": "fetch"}, ["retry"]),
        ("analyze", {"stage": "dispatch"}, ["retry", "contact_platform"]),
    ],
)
def test_each_cause_offers_its_actions(client, world, kind, report, actions):
    run = _failed(world, kind, **report)

    assert _detail(client, run)["failure"]["actions"] == actions


def test_a_build_of_an_analysis_of_another_branch_offers_a_reanalysis(
    client, world
):
    run = _failed(world, stage="build")
    with world.db() as db:
        db.get(ImportRun, run.id).source.branch = "develop"
        db.commit()

    assert _detail(client, run)["failure"]["actions"] == [
        "fix_in_repo",
        "retry",
        "reanalyze",
    ]


def test_a_run_in_progress_has_no_failure(client, world):
    run = _build(world)

    assert _detail(client, run)["failure"] is None


# ── Dead workflows ──────────────────────────────────────────────────────────


class GitHub:
    def __init__(self):
        self.listing: list[dict] = []
        self.runs: dict[str, dict] = {}
        self.calls: list[str] = []
        self.error: Exception | None = None

    def install(self, monkeypatch):
        async def token():
            self.calls.append("token")
            return "ghs_installation"

        async def listing(token, repo, workflow, since):
            self.calls.append("list")
            if self.error:
                raise self.error
            return self.listing

        async def read(token, repo, workflow_run_id):
            self.calls.append(f"get {workflow_run_id}")
            return self.runs.get(workflow_run_id)

        monkeypatch.setattr(watch, "get_org_installation_token", token)
        monkeypatch.setattr(watch, "list_workflow_runs", listing)
        monkeypatch.setattr(watch, "get_workflow_run", read)


@pytest.fixture
def github(world, monkeypatch):
    fake = GitHub()
    fake.install(monkeypatch)
    monkeypatch.setattr(watch, "SessionLocal", world.Session)
    monkeypatch.setattr(
        watch.audit, "record_system_event", lambda *a, **k: None
    )
    return fake


def _ago(minutes: int) -> str:
    moment = service._now() - timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _age(world, run, minutes: int) -> None:
    with world.db() as db:
        row = db.get(ImportRun, run.id)
        row.created_at = row.updated_at = service._now() - timedelta(
            minutes=minutes
        )
        db.commit()


def _workflow(run, status="completed", conclusion="failure", ended=5):
    return {
        "id": 900,
        "display_title": f"CNP import {run.id}",
        "status": status,
        "conclusion": conclusion,
        "updated_at": _ago(ended),
        "html_url": ACTIONS_URL,
    }


def _status(world, run) -> str:
    with world.db() as db:
        return db.get(ImportRun, run.id).status


def test_a_run_whose_workflow_failed_unclaimed_is_failed(world, github):
    run = _build(world, status="queued")
    _age(world, run, 6)
    github.listing = [_workflow(run, conclusion="startup_failure")]

    asyncio.run(watch.check_workflows())

    with world.db() as db:
        report = db.get(ImportRun, run.id).report
    assert (report["stage"], report["failure"]["cause"]) == (
        "workflow",
        "workflow_stopped",
    )


def test_a_claimed_run_is_found_by_its_workflow_run_id(world, github):
    run = _build(world, status="claimed", workflow_run_id="777")
    _age(world, run, 30)
    github.runs["777"] = _workflow(run, conclusion="cancelled")

    asyncio.run(watch.check_workflows())

    assert (_status(world, run), github.calls) == (
        "failed",
        ["token", "get 777"],
    )


def test_a_workflow_still_running_leaves_the_run_alone(world, github):
    run = _build(world, status="claimed", workflow_run_id="777")
    _age(world, run, 30)
    github.runs["777"] = _workflow(run, status="in_progress")

    asyncio.run(watch.check_workflows())

    assert _status(world, run) == "claimed"


def test_a_workflow_just_ended_has_time_to_report(world, github):
    run = _build(world, status="claimed", workflow_run_id="777")
    _age(world, run, 30)
    github.runs["777"] = _workflow(run, ended=0)

    asyncio.run(watch.check_workflows())

    assert _status(world, run) == "claimed"


def test_a_dispatch_github_never_started_fails_after_ten_minutes(
    world, github
):
    run = _build(world, status="queued")
    _age(world, run, 11)

    asyncio.run(watch.check_workflows())

    with world.db() as db:
        cause = db.get(ImportRun, run.id).report["failure"]["cause"]
    assert cause == "workflow_not_started"


def test_a_recent_dispatch_not_listed_yet_is_left_alone(world, github):
    run = _build(world, status="queued")
    _age(world, run, 4)

    asyncio.run(watch.check_workflows())

    assert _status(world, run) == "queued"


def test_github_unavailable_fails_nothing(world, github):
    run = _build(world, status="queued")
    _age(world, run, 60)
    github.error = service.GitHubAppError("HTTP 502")

    asyncio.run(watch.check_workflows())

    assert _status(world, run) == "queued"


def test_no_active_run_costs_no_github_call(world, github):
    _build(world, status="failed")

    asyncio.run(watch.check_workflows())

    assert github.calls == []


def test_a_dead_workflow_offers_a_retry_without_its_url(client, world, github):
    run = _build(world, status="claimed", workflow_run_id="777")
    _age(world, run, 30)
    github.runs["777"] = _workflow(run, conclusion="cancelled")
    asyncio.run(watch.check_workflows())

    failure = _detail(client, run)["failure"]

    assert (failure["actions"], ACTIONS_URL in str(failure)) == (
        ["retry"],
        False,
    )


def test_the_loop_watches_workflows_more_often_than_it_reconciles(
    monkeypatch,
):
    calls = []

    async def reconcile():
        calls.append("reconcile")

    async def check():
        calls.append("watch")

    ticks = iter(range(3))

    async def sleep(_seconds):
        if next(ticks) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(rebuilds, "reconcile_once", reconcile)
    monkeypatch.setattr(rebuilds.watch, "check_workflows", check)
    monkeypatch.setattr(rebuilds.asyncio, "sleep", sleep)
    monkeypatch.setattr(settings, "IMPORT_RECONCILE_INTERVAL_SECONDS", 3600)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rebuilds.reconcile_loop())

    assert calls == ["reconcile", "watch", "watch", "watch"]


def test_public_text_strips_only_the_workflow_repository_links():
    text = failures.public_text(
        f"failed, see {ACTIONS_URL} and https://github.com/org/app/issues/1"
    )

    assert text == "failed and https://github.com/org/app/issues/1"
