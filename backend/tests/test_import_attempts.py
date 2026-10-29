"""Attempt identity, setup failure and immutable recipe context at the CMP boundary."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, TargetCloud
from app.models.repository_import import RepositoryImport
from app.models.user_github import UserGitHubInstallation
from app.routers.repository_imports import ImportRequest, list_imports
from app.services import repository_imports as service

ATTEMPT = "12345678-1234-4234-8234-123456789012"
OLD_ATTEMPT = "87654321-4321-4321-8321-210987654321"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def job(db, monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_WORKFLOW_SHA", "a" * 40)
    monkeypatch.setattr(
        settings, "IMPORT_WORKFLOW_REF", "import-fichier-joe-raph"
    )
    value = RepositoryImport(
        id="11111111-1111-4111-8111-111111111111",
        project_name="sandbox",
        app_name="web",
        repository_url="https://github.com/example/original",
        user_sub="user-id",
        username="joe",
        installation_id="11",
        status="queued",
        report={},
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1),
        configuration={
            **service.import_context(),
            "attempt_id": ATTEMPT,
            "source_branch": "recipe",
            "target_cloud": "onprem",
            "chart_sha": "b" * 40,
            "hostname": f"web-sandbox.{settings.IMPORT_DOMAIN}",
            "image": "ghcr.io/example/web:import-test",
        },
    )
    db.add(value)
    db.commit()
    return value


def claims(run_id="123"):
    return {
        "run_id": run_id,
        "sha": settings.IMPORT_WORKFLOW_SHA,
        "ref": "refs/heads/" + settings.IMPORT_WORKFLOW_REF,
        "event_name": "workflow_dispatch",
        "run_attempt": "1",
    }


def metadata(job):
    workflow = {
        "id": 42,
        "path": ".github/workflows/" + settings.IMPORT_DISPATCH_WORKFLOW,
    }
    run = {
        "id": 123,
        "workflow_id": 42,
        "display_title": f"CNP import {job.id} attempt {ATTEMPT}",
        "repository": {"full_name": settings.IMPORT_WORKFLOW_REPO},
        "head_repository": {"full_name": settings.IMPORT_WORKFLOW_REPO},
        "head_sha": settings.IMPORT_WORKFLOW_SHA,
        "head_branch": settings.IMPORT_WORKFLOW_REF,
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "status": "in_progress",
        "conclusion": None,
    }
    return workflow, run


def callback(db, job, action, payload=None, run_id="123", tasks=None):
    return asyncio.run(
        service.worker(
            db,
            job,
            claims(run_id),
            action,
            {
                "attempt_id": job.configuration.get("attempt_id"),
                **(payload or {}),
            },
            tasks or BackgroundTasks(),
        )
    )


def test_setup_failure_binds_the_exact_run_and_stops_without_source_access(
    db, job
):
    workflow, run = metadata(job)
    github = AsyncMock(side_effect=[workflow, run])
    with patch.object(
        service, "get_installation_token", AsyncMock(return_value="test")
    ) as token, patch.object(service, "github", github), patch.object(
        service, "authorize", AsyncMock()
    ) as authorize:
        result = callback(db, job, "fail", {"report": {"setup": "failed"}})
        assert callback(db, job, "fail") == {"status": "failed"}
    assert result == {"status": "failed"}
    assert (
        job.run_id == "123"
        and job.source_sha is None
        and job.status == "failed"
    )
    assert job.report == {"setup": "failed"}
    assert db.query(Deployment).count() == 0
    authorize.assert_not_awaited()
    token.assert_awaited_once_with(
        settings.GITHUB_INSTALLATION_ID, permissions={"actions": "read"}
    )
    assert [call.args[:2] for call in github.call_args_list] == [
        (
            "GET",
            f"/repos/{settings.IMPORT_WORKFLOW_REPO}/actions/workflows/{settings.IMPORT_DISPATCH_WORKFLOW}",
        ),
        ("GET", f"/repos/{settings.IMPORT_WORKFLOW_REPO}/actions/runs/123"),
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 999),
        ("workflow_id", 99),
        ("head_sha", "wrong"),
        ("head_branch", "other"),
        ("event", "push"),
        ("run_attempt", 2),
        ("repository", {"full_name": "other/repo"}),
        ("head_repository", {"full_name": "other/fork"}),
        ("display_title", "CNP import another-job"),
        (
            "display_title",
            "CNP import 11111111-1111-4111-8111-111111111111 attempt "
            + OLD_ATTEMPT,
        ),
    ],
)
def test_unrelated_or_stale_run_cannot_stop_a_queued_attempt(
    db, job, field, value
):
    workflow, run = metadata(job)
    run[field] = value
    with patch.object(
        service, "get_installation_token", AsyncMock(return_value="test")
    ), patch.object(service, "github", AsyncMock(side_effect=[workflow, run])):
        with pytest.raises(HTTPException) as error:
            callback(db, job, "fail")
    assert error.value.status_code == 403
    assert job.status == "queued" and job.run_id is None


def test_workflow_lookup_must_identify_the_configured_caller(db, job):
    workflow, run = metadata(job)
    workflow["path"] = ".github/workflows/another.yml"
    with patch.object(
        service, "get_installation_token", AsyncMock(return_value="test")
    ), patch.object(service, "github", AsyncMock(side_effect=[workflow, run])):
        with pytest.raises(HTTPException):
            callback(db, job, "fail")
    assert job.status == "queued" and job.run_id is None


def test_metadata_unavailable_does_not_invent_a_run_binding(db, job):
    with patch.object(
        service,
        "get_installation_token",
        AsyncMock(side_effect=HTTPException(503, "unavailable")),
    ):
        with pytest.raises(HTTPException):
            callback(db, job, "fail")
    assert job.status == "queued" and job.run_id is None


def test_preclaim_failure_cannot_replace_a_concurrent_claim(db, job):
    async def competing_claim(*_):
        with sessionmaker(bind=db.get_bind())() as other:
            other.execute(
                update(RepositoryImport)
                .where(RepositoryImport.id == job.id)
                .values(status="preparing", run_id="456")
            )
            other.commit()

    with patch.object(
        service,
        "verify_unclaimed_failure",
        AsyncMock(side_effect=competing_claim),
    ):
        with pytest.raises(HTTPException):
            callback(db, job, "fail")
    db.refresh(job)
    assert job.status == "preparing" and job.run_id == "456"


@pytest.mark.parametrize(
    "action", ["claim", "write", "validated", "publish", "complete", "fail"]
)
def test_old_attempt_is_refused_before_any_external_access(db, job, action):
    with patch.object(
        service, "authorize", AsyncMock()
    ) as authorize, patch.object(
        service, "get_installation_token", AsyncMock()
    ) as token:
        with pytest.raises(HTTPException):
            callback(db, job, action, {"attempt_id": OLD_ATTEMPT})
    authorize.assert_not_awaited()
    token.assert_not_awaited()
    assert job.status == "queued" and job.run_id is None


def test_legacy_queued_job_cannot_bind_an_unverified_failure(db, job):
    job.configuration = {
        key: value
        for key, value in job.configuration.items()
        if key != "attempt_id"
    }
    db.commit()
    with patch.object(service, "get_installation_token", AsyncMock()) as token:
        with pytest.raises(HTTPException):
            callback(db, job, "fail")
    token.assert_not_awaited()
    assert job.status == "queued" and job.run_id is None


@pytest.mark.parametrize(
    "key,setting",
    [
        ("registry_repo", "CNP_REGISTRY_REPO"),
        ("registry_branch", "CNP_REGISTRY_BRANCH"),
        ("domain", "IMPORT_DOMAIN"),
    ],
)
def test_recipe_context_change_stops_before_project_or_github_access(
    db, job, monkeypatch, key, setting
):
    monkeypatch.setattr(settings, setting, "another-value")
    with patch.object(service, "enabled"), patch.object(
        service, "fresh_project"
    ) as project, patch.object(service, "scoped_token", AsyncMock()) as token:
        with pytest.raises(HTTPException):
            asyncio.run(service.authorize(db, job))
    project.assert_not_called()
    token.assert_not_awaited()


def test_legacy_context_cannot_silently_adopt_the_current_registry(db, job):
    job.configuration = {
        key: value
        for key, value in job.configuration.items()
        if key not in service.import_context()
    }
    with patch.object(service, "enabled"), patch.object(
        service, "scoped_token", AsyncMock()
    ) as token:
        with pytest.raises(HTTPException):
            asyncio.run(service.authorize(db, job))
    token.assert_not_awaited()


def test_create_snapshots_context_and_persists_nonce_before_dispatch(db):
    project = Project(
        project_name="sandbox",
        owner_username="joe",
        target_cloud=TargetCloud.ONPREM,
    )
    db.add(project)
    db.add(UserGitHubInstallation(user_sub="user-id", installation_id="11"))
    db.commit()
    dispatched = []

    async def dispatch(value, **_kwargs):
        stored = db.get(RepositoryImport, value.id)
        assert (
            str(UUID(stored.configuration["attempt_id"]))
            == stored.configuration["attempt_id"]
        )
        assert all(
            stored.configuration[key] == expected
            for key, expected in service.import_context().items()
        )
        dispatched.append(stored.id)

    request = ImportRequest(
        project_name="sandbox",
        app_name="web",
        repository_url="https://github.com/example/original",
        port=8000,
        allow_changes=True,
    )
    with patch.object(
        service, "fresh_project", return_value=project
    ), patch.object(
        service, "authorize", AsyncMock(return_value="test")
    ), patch.object(
        service, "head", AsyncMock(return_value="a" * 40)
    ), patch.object(
        service, "dispatch", AsyncMock(side_effect=dispatch)
    ):
        result = asyncio.run(
            service.create(
                db, {"sub": "user-id", "preferred_username": "joe"}, request
            )
        )
    assert dispatched == [result["id"]]
    assert db.query(Project).count() == 1 and db.query(Deployment).count() == 0


def test_dispatch_sends_both_import_and_attempt_identifiers(job):
    with patch.object(
        service,
        "require_workflow_configuration",
        AsyncMock(return_value="test"),
    ), patch.object(service, "github", AsyncMock()) as github:
        asyncio.run(service.dispatch(job))
    assert github.call_args.kwargs["json"]["inputs"] == {
        "import_id": job.id,
        "attempt_id": ATTEMPT,
    }


def test_retry_pr_resume_callbacks_and_final_reconciliation_keep_attempts_separate(
    db, job
):
    job.status, job.run_id = "failed", "old-run"
    db.commit()
    tasks = BackgroundTasks()
    head = AsyncMock(return_value="a" * 40)
    pr = {
        "head": {"ref": f"cnp-import/{job.id}"},
        "base": {"ref": "recipe"},
        "state": "open",
    }
    with patch.object(
        service, "authorize", AsyncMock(return_value="test")
    ), patch.object(service, "head", head), patch.object(
        service, "platform_chart_token", AsyncMock(return_value="chart")
    ), patch.object(
        service, "scoped_token", AsyncMock(return_value="write")
    ), patch.object(
        service, "github", AsyncMock(side_effect=[[], [], pr])
    ), patch.object(
        service, "dispatch", AsyncMock()
    ) as dispatch:
        asyncio.run(service.retry(db, job))
        first = job.configuration["attempt_id"]
        assert first != ATTEMPT
        with pytest.raises(HTTPException):
            callback(
                db, job, "fail", {"attempt_id": ATTEMPT}, run_id="old-run"
            )
        contract = callback(db, job, "claim")
        assert contract["attempt_id"] == first
        for _ in range(3):
            callback(db, job, "write")
        callback(db, job, "prepared", {"pull_request": 7})
        assert job.status == "waiting_pr" and db.query(Deployment).count() == 0
        assert callback(db, job, "fail") == {"status": "waiting_pr"}
        with patch.object(
            service,
            "approved_pr",
            AsyncMock(side_effect=HTTPException(409, "not approved")),
        ):
            with pytest.raises(HTTPException):
                asyncio.run(service.resume(db, job))
        assert job.status == "waiting_pr" and dispatch.await_count == 1
        with patch.object(service, "approved_pr", AsyncMock()):
            asyncio.run(service.resume(db, job))
            assert job.configuration["attempt_id"] != first
            with pytest.raises(HTTPException):
                callback(db, job, "fail", {"attempt_id": first})
            head.return_value = "c" * 40
            callback(db, job, "claim", run_id="456")
        callback(
            db,
            job,
            "validated",
            {
                "source_sha": "c" * 40,
                "report": {
                    name: {"blocked": False}
                    for name in (
                        "security",
                        "deployment",
                        "rendered_deployment",
                    )
                },
            },
            run_id="456",
        )
        callback(
            db,
            job,
            "publish",
            {
                "image": job.configuration["image"],
                "source_sha": "c" * 40,
                "image_size": 1234,
                "image_scan_passed": True,
                "http_check_passed": True,
            },
            run_id="456",
        )
        published = {
            "image": job.configuration["image"],
            "digest": "sha256:" + "d" * 64,
        }
        with patch.object(
            settings, "IMPORT_DOMAIN", "changed-after-authorization.invalid"
        ):
            callback(db, job, "complete", published, run_id="456", tasks=tasks)
        with pytest.raises(HTTPException):
            callback(db, job, "complete", published, run_id="456", tasks=tasks)
    deployment = db.query(Deployment).one()
    assert (
        json.loads(deployment.app_config)["domain"]
        == job.configuration["domain"]
    )
    assert (
        deployment.github_repo_url == job.repository_url
        and deployment.project_id == job.project_name
    )
    assert len(tasks.tasks) == 1
    deployment.status = DeploymentStatus.RUNNING
    db.commit()
    with patch.object(service, "fresh_project"):
        result = list_imports(
            job.project_name,
            db,
            {"sub": job.user_sub, "preferred_username": job.username},
        )
    assert result[0]["status"] == "completed"


def replace_from_other_session(
    db, job_id, *, attempt_id=OLD_ATTEMPT, status="queued", run_id=None
):
    """A real second session cancels A and installs B while A awaits a service."""
    with sessionmaker(bind=db.get_bind(), autoflush=True)() as other:
        current = other.get(RepositoryImport, job_id)
        current.status = "cancelled"
        other.commit()
        current.configuration = {
            **current.configuration,
            "attempt_id": attempt_id,
            "winner": "new attempt",
        }
        current.status, current.run_id = status, run_id
        current.expires_at = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) + timedelta(hours=1)
        current.message = "New attempt intact"
        other.commit()


def assert_winner_intact(
    db, job, *, attempt_id=OLD_ATTEMPT, status="queued", run_id=None
):
    with db.no_autoflush:
        db.refresh(job)
    assert job.configuration["attempt_id"] == attempt_id
    assert job.configuration["winner"] == "new attempt"
    assert "old_dirty" not in job.configuration
    assert job.status == status and job.run_id == run_id
    assert job.message == "New attempt intact"


def test_transition_cas_prevents_autoflush_from_restoring_old_configuration(
    db, job
):
    assert db.autoflush is True
    owner = service.job_owner(job)
    job.configuration = {**job.configuration, "old_dirty": True}
    replace_from_other_session(db, job.id)
    with pytest.raises(HTTPException):
        service.transition(db, job, "queued", "failed", expected_owner=owner)
    assert_winner_intact(db, job)


def test_owned_transition_persists_new_nonce_and_dirty_grant_in_the_same_update(
    db, job
):
    owner = service.job_owner(job)
    job.configuration = {
        **job.configuration,
        "attempt_id": OLD_ATTEMPT,
        "repository_id": 55,
    }
    deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        hours=2
    )
    job.expires_at = deadline
    service.transition(db, job, "queued", "queued", expected_owner=owner)
    db.refresh(job)
    assert job.configuration["attempt_id"] == OLD_ATTEMPT
    assert (
        job.configuration["repository_id"] == 55 and job.expires_at == deadline
    )


def test_legacy_null_nonce_does_not_match_a_new_attempt(db, job):
    job.configuration = {
        key: value
        for key, value in job.configuration.items()
        if key != "attempt_id"
    }
    db.commit()
    owner = service.job_owner(job)
    assert owner == (None, None)
    replace_from_other_session(db, job.id)
    with pytest.raises(HTTPException):
        service.transition(db, job, "queued", "failed", expected_owner=owner)
    assert_winner_intact(db, job)


def test_delayed_preclaim_failure_cannot_stop_a_cancelled_and_retried_job(
    db, job
):
    workflow, run = metadata(job)
    job.configuration = {**job.configuration, "old_dirty": True}

    async def github(_method, path, _token):
        if "/runs/" in path:
            replace_from_other_session(db, job.id)
            return run
        return workflow

    with patch.object(
        service, "get_installation_token", AsyncMock(return_value="test")
    ), patch.object(service, "github", AsyncMock(side_effect=github)):
        with pytest.raises(HTTPException):
            callback(db, job, "fail")
    assert_winner_intact(db, job)


@pytest.mark.parametrize("same_attempt_winner", [False, True])
def test_delayed_claim_and_its_error_handler_cannot_replace_the_winning_owner(
    db, job, same_attempt_winner
):
    winner = (
        {"attempt_id": ATTEMPT, "status": "preparing", "run_id": "456"}
        if same_attempt_winner
        else {}
    )

    async def authorize(*_args, **_kwargs):
        job.configuration = {**job.configuration, "old_dirty": True}
        replace_from_other_session(db, job.id, **winner)
        # A query here used to autoflush the stale grant before the CAS.
        db.query(RepositoryImport).count()
        return "test"

    with patch.object(
        service, "authorize", AsyncMock(side_effect=authorize)
    ), patch.object(
        service, "head", AsyncMock(return_value="a" * 40)
    ), patch.object(
        service, "platform_chart_token", AsyncMock(return_value="chart")
    ):
        with pytest.raises(HTTPException):
            callback(db, job, "claim")
    assert_winner_intact(db, job, **winner)


@pytest.mark.parametrize("same_attempt_winner", [False, True])
def test_old_authorization_exception_cannot_fail_the_new_or_claimed_owner(
    db, job, same_attempt_winner
):
    winner = (
        {"attempt_id": ATTEMPT, "status": "preparing", "run_id": "456"}
        if same_attempt_winner
        else {}
    )

    async def authorize(*_args, **_kwargs):
        job.configuration = {**job.configuration, "old_dirty": True}
        replace_from_other_session(db, job.id, **winner)
        raise HTTPException(503, "old request failed")

    with patch.object(service, "authorize", AsyncMock(side_effect=authorize)):
        with pytest.raises(HTTPException):
            callback(db, job, "claim")
    assert_winner_intact(db, job, **winner)


def test_chart_failure_after_a_successful_claim_still_stops_its_own_attempt(
    db, job
):
    with patch.object(
        service, "authorize", AsyncMock(return_value="test")
    ), patch.object(
        service, "head", AsyncMock(return_value="a" * 40)
    ), patch.object(
        service,
        "platform_chart_token",
        AsyncMock(side_effect=HTTPException(503, "chart unavailable")),
    ):
        with pytest.raises(HTTPException):
            callback(db, job, "claim")
    assert job.status == "failed" and job.run_id == "123"
    assert job.configuration["attempt_id"] == ATTEMPT


def test_completion_cas_includes_nonce_and_never_creates_a_stale_deployment(
    db, job
):
    job.status, job.run_id, job.source_sha = "publishing", "123", "a" * 40
    db.commit()

    async def head(*_):
        job.configuration = {**job.configuration, "old_dirty": True}
        replace_from_other_session(
            db, job.id, status="publishing", run_id="123"
        )
        return "a" * 40

    with patch.object(
        service, "authorize", AsyncMock(return_value="test")
    ), patch.object(service, "head", AsyncMock(side_effect=head)):
        with pytest.raises(HTTPException):
            callback(
                db,
                job,
                "complete",
                {
                    "image": job.configuration["image"],
                    "digest": "sha256:" + "d" * 64,
                },
            )
    assert_winner_intact(db, job, status="publishing", run_id="123")
    assert db.query(Deployment).count() == 0


@pytest.mark.parametrize("action", ["resume", "retry"])
def test_portal_restart_cas_uses_the_nonce_before_generating_its_replacement(
    db, job, action
):
    job.status = "waiting_pr" if action == "resume" else "failed"
    job.pull_request = 7 if action == "resume" else None
    job.run_id = "123"
    db.commit()

    async def changed_while_awaiting(*_args, **_kwargs):
        job.configuration = {**job.configuration, "old_dirty": True}
        replace_from_other_session(db, job.id, status=job.status, run_id="123")
        return []

    with patch.object(
        service, "authorize", AsyncMock(return_value="test")
    ), patch.object(
        service, "approved_pr", AsyncMock(side_effect=changed_while_awaiting)
    ), patch.object(
        service, "github", AsyncMock(side_effect=changed_while_awaiting)
    ), patch.object(
        service, "dispatch", AsyncMock()
    ) as dispatch:
        with pytest.raises(HTTPException):
            asyncio.run(getattr(service, action)(db, job))
    assert_winner_intact(
        db,
        job,
        status="waiting_pr" if action == "resume" else "failed",
        run_id="123",
    )
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("action", ["create", "resume", "retry"])
def test_old_dispatch_exception_cannot_fail_a_later_restart(db, job, action):
    project = Project(
        project_name="sandbox",
        owner_username="joe",
        target_cloud=TargetCloud.ONPREM,
    )
    db.add(project)
    db.add(UserGitHubInstallation(user_sub="user-id", installation_id="11"))
    if action == "create":
        db.delete(job)
    else:
        job.status = "waiting_pr" if action == "resume" else "failed"
        job.pull_request = 7 if action == "resume" else None
    db.commit()
    dispatched_job = []

    async def dispatch(value, *, attempt_id=None):
        assert attempt_id == value.configuration["attempt_id"]
        dispatched_job.append(value)
        value.configuration = {**value.configuration, "old_dirty": True}
        replace_from_other_session(db, value.id)
        raise RuntimeError("old dispatch failed after retry")

    request = ImportRequest(
        project_name="sandbox",
        app_name="web",
        repository_url="https://github.com/example/original",
        port=8000,
        allow_changes=True,
    )
    with patch.object(
        service, "fresh_project", return_value=project
    ), patch.object(
        service, "authorize", AsyncMock(return_value="test")
    ), patch.object(
        service, "head", AsyncMock(return_value="a" * 40)
    ), patch.object(
        service, "approved_pr", AsyncMock()
    ), patch.object(
        service, "github", AsyncMock(return_value=[])
    ), patch.object(
        service, "dispatch", AsyncMock(side_effect=dispatch)
    ):
        with pytest.raises(RuntimeError):
            if action == "create":
                asyncio.run(
                    service.create(
                        db,
                        {"sub": "user-id", "preferred_username": "joe"},
                        request,
                    )
                )
            else:
                asyncio.run(getattr(service, action)(db, job))
    assert_winner_intact(db, dispatched_job[0])


def test_stale_list_expiration_does_not_fail_a_new_attempt(db, job):
    job.expires_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) - timedelta(seconds=1)
    db.commit()

    class RacingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            replace_from_other_session(db, job.id)
            return datetime.now(tz)

    with patch.object(service, "fresh_project"), patch(
        "datetime.datetime", RacingClock
    ):
        result = list_imports(
            job.project_name,
            db,
            {"sub": job.user_sub, "preferred_username": job.username},
        )
    assert result[0]["status"] == "queued"
    assert_winner_intact(db, job)


def test_old_remote_cancellation_failure_does_not_overwrite_the_retry_message(
    db, job
):
    from app.routers import repository_imports as routes

    job.status, job.run_id = "preparing", "123"
    db.commit()

    async def unavailable(*_args, **_kwargs):
        replace_from_other_session(db, job.id)
        raise HTTPException(503, "cancel unavailable")

    with patch.object(service, "fresh_project"), patch.object(
        service, "get_installation_token", AsyncMock(side_effect=unavailable)
    ):
        result = asyncio.run(
            routes.cancel(
                job.id,
                db,
                {"sub": job.user_sub, "preferred_username": job.username},
            )
        )
    assert (
        result["status"] == "queued"
        and result["message"] == "New attempt intact"
    )
    assert_winner_intact(db, job)
