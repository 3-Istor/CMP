"""
CNP-310: an assistant reports a platform difficulty with its user's consent;
platform admins read it in the CMP.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import mcp_server
from app.core.database import Base, get_db
from app.models.issue_report import IssueReport, IssueReportSubmission
from app.routers import issue_reports as router
from app.services import issue_reports
from app.services.agents import internal_api
from app.services.keycloak_service import get_current_user

TOKEN = "ghp_" + "Q7x" * 12
ALICE_ASSISTANT = {
    "preferred_username": "alice",
    "cnp_agent": {"client": "Claude Code"},
}
INFRA = {"preferred_username": "ivan", "groups": ["/infra"]}
INFRA_ASSISTANT = {**INFRA, "cnp_agent": {"client": "Codex"}}
TOKENS = {
    "alice-assistant": ALICE_ASSISTANT,
    "infra": INFRA,
    "infra-assistant": INFRA_ASSISTANT,
}
REPORT = {
    "category": "missing_tool",
    "summary": "No tool to read the app's cron jobs",
    "tool": "get_app",
    "steps": "Looked for cron jobs in get_app and get_app_config.",
}


@pytest.fixture
def cmp(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    api = FastAPI()
    api.include_router(router.router, prefix="/api")

    def current_user(authorization: str = Header("")):
        return TOKENS[authorization.removeprefix("Bearer ")]

    def db():
        with factory() as session:
            yield session

    api.dependency_overrides[get_current_user] = current_user
    api.dependency_overrides[get_db] = db
    client = TestClient(api)

    async def call(method, path, **kwargs):
        response = client.request(
            method,
            path,
            headers={"Authorization": "Bearer alice-assistant"},
            **kwargs,
        )
        if response.status_code >= 400:
            raise internal_api.ApiError(
                response.status_code, response.json()["detail"]
            )
        return response.json()

    caller = SimpleNamespace(api=call, username="alice")
    monkeypatch.setattr(mcp_server, "Caller", lambda: caller)
    client.sessions = factory
    return client


def _report(**arguments) -> dict:
    return asyncio.run(mcp_server.report_issue(**{**REPORT, **arguments}))


def _admin_view(client, who="infra"):
    return client.get(
        "/api/issue-reports", headers={"Authorization": f"Bearer {who}"}
    )


def _stored(client) -> list[IssueReport]:
    with client.sessions() as db:
        return db.query(IssueReport).all()


def test_a_confirmed_report_appears_in_the_admin_view(cmp):
    _report(user_confirmed=True)

    shown = _admin_view(cmp).json()

    assert [(r["summary"], r["reporters"], r["assistant"]) for r in shown] == [
        (REPORT["summary"], ["alice"], "Claude Code")
    ]


def test_without_confirmation_nothing_is_stored(cmp):
    answer = _report()

    assert (answer["status"], _stored(cmp)) == ("needs_user_confirmation", [])


def test_the_confirmation_shows_the_user_what_will_be_sent(cmp):
    answer = _report(steps=f"Called with {TOKEN}")

    assert answer["report"]["steps"] == "Called with [redacted]"


def test_a_report_with_a_token_is_stored_without_it(cmp):
    _report(
        steps=f"get_app failed with Authorization: token {TOKEN}",
        user_confirmed=True,
    )

    assert TOKEN not in (_stored(cmp)[0].steps or "")


def test_the_eleventh_report_of_the_day_is_refused(cmp):
    for n in range(issue_reports.DAILY_QUOTA):
        _report(
            summary=f"Report number {n} about the platform",
            user_confirmed=True,
        )

    answer = _report(summary="One report too many today", user_confirmed=True)

    assert (answer["ok"], answer["status"]) == (False, 429)


def test_the_same_report_twice_is_stored_once_and_counted_once(cmp):
    _report(user_confirmed=True)

    again = _report(user_confirmed=True)

    assert (again["already_known"], len(_stored(cmp))) == (True, 1)


def test_another_user_reporting_the_same_thing_adds_an_occurrence(cmp):
    with cmp.sessions() as db:
        issue_reports.submit(db, "bob", None, **REPORT)

    _report(summary=REPORT["summary"].upper(), user_confirmed=True)

    stored = _stored(cmp)
    assert [(r.occurrences, r.reporters) for r in stored] == [
        (2, ["bob", "alice"])
    ]


def test_what_is_stored_is_what_the_user_was_shown(cmp):
    shown = _report(steps=f"Called with {TOKEN}")["report"]

    _report(steps=f"Called with {TOKEN}", user_confirmed=True)

    stored = _admin_view(cmp).json()[0]
    assert {k: stored[k] for k in shown} == shown


def test_a_preview_stores_nothing(cmp):
    answer = _report(preview=True, user_confirmed=True)

    assert (answer["preview"], _stored(cmp)) == (True, [])


@pytest.mark.parametrize("who", ["alice-assistant", "infra-assistant"])
def test_only_a_platform_admin_in_person_reads_the_reports(cmp, who):
    assert _admin_view(cmp, who).status_code == 403


def test_an_admin_marks_a_report_exported(cmp):
    _report(user_confirmed=True)
    report_id = _admin_view(cmp).json()[0]["id"]

    cmp.patch(
        f"/api/issue-reports/{report_id}",
        json={"status": "exported"},
        headers={"Authorization": "Bearer infra"},
    )

    assert _admin_view(cmp).json()[0]["status"] == "exported"


def test_a_summary_past_the_size_limit_is_refused(cmp):
    response = cmp.post(
        "/api/issue-reports",
        json={**REPORT, "summary": "x" * (issue_reports.SUMMARY_MAX + 1)},
        headers={"Authorization": "Bearer alice-assistant"},
    )

    assert response.status_code == 422


def test_purge_drops_stale_reports_and_old_quota_rows(cmp):
    _report(user_confirmed=True)
    _report(user_confirmed=True, summary="Another difficulty")
    with cmp.sessions() as db:
        stale, fresh = db.query(IssueReport).order_by(IssueReport.id).all()
        long_ago = stale.last_reported_at - timedelta(
            days=issue_reports.REPORT_RETENTION_DAYS + 1
        )
        stale.last_reported_at = long_ago
        db.query(IssueReportSubmission).update(
            {
                IssueReportSubmission.created_at: fresh.created_at
                - timedelta(days=issue_reports.SUBMISSION_RETENTION_DAYS + 1)
            }
        )
        db.commit()

        issue_reports.purge_expired(db)

        assert [r.summary for r in db.query(IssueReport).all()] == [
            "Another difficulty"
        ]
        assert db.query(IssueReportSubmission).count() == 0
