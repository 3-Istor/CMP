import asyncio
from datetime import date, datetime, timedelta
from unittest import mock

import pytest
from fastapi import HTTPException, Request

from app.models.security import SecurityException
from app.routers import security
from app.services import keycloak_service
from app.services.security import exceptions as rules
from app.services.security import store
from app.services.security.model import Audience, Category, Draft, Source, Tier
from tests.test_platform_only_endpoints import session_factory  # noqa: F401

NOW = datetime(2026, 11, 11, 12, 0)
TODAY = NOW.date()
DEV = {"sub": "d", "preferred_username": "dev"}
OTHER = {"sub": "o", "preferred_username": "other"}
AUDITOR = {
    "sub": "a",
    "preferred_username": "aud",
    "groups": ["security-auditor"],
}
TITLE = {"key": "leak.title", "params": {}}


@pytest.fixture(autouse=True)
def fixed_day():
    with mock.patch.object(security, "utcnow", lambda: NOW):
        yield


def _finding(db, project="shop", fp="f1", tier=Tier.IMPORTANT):
    draft = Draft(
        fingerprint=fp,
        app="web",
        source=Source.CI,
        category=Category.DEPENDENCIES,
        tier=tier,
        audience=Audience.DEVELOPER,
        rule="CVE-1",
        title="Leak",
        i18n='{"title": {"key": "leak.title", "params": {}}}',
    )
    # No app given: adds the finding without resolving the others.
    store.upsert_findings(db, project, [draft], (Source.CI,), set(), NOW)


def _exception(db, project="shop", fp="f1", **kw):
    fields = {
        "project": project,
        "app": "web",
        "fingerprint": fp,
        "rule": "CVE-1",
        "kind": "vulnerability",
        "status": "not_affected",
        "statement": "never called in our code",
        "expires_on": TODAY + timedelta(days=30),
        "author": "dev",
    }
    fields.update(kw)
    row = SecurityException(**fields)
    db.add(row)
    db.commit()
    return row


def _role(role):
    return mock.patch.object(
        keycloak_service, "get_project_role", lambda u, p: role
    )


def _sync():
    return mock.patch.object(
        security, "_sync_exceptions_file", mock.AsyncMock(return_value=None)
    )


def _list(factory, token=DEV, **kw):
    with factory() as db, _role("member"):
        return asyncio.run(
            security.list_exceptions(token, "shop", db=db, **kw)
        )


def test_exception_read_carries_the_finding_title(session_factory):
    with session_factory() as db:
        _finding(db)
        _exception(db)

    [read] = _list(session_factory)

    assert read.title == "Leak"
    assert read.i18n == {"title": TITLE}
    assert read.project == "shop"


def test_an_expired_exception_is_listed_as_expired(session_factory):
    with session_factory() as db:
        _finding(db)
        _exception(
            db, status="accepted_risk", expires_on=TODAY - timedelta(days=1)
        )

    [read] = _list(session_factory)

    assert read.expired is True
    assert read.pending_approval is False


def test_a_core_exception_without_a_reviewer_approval_is_pending(
    session_factory,
):
    with session_factory() as db:
        _finding(db, tier=Tier.CORE)
        _exception(db, approved_at=NOW, approved_by="boss")

    [read] = _list(session_factory)

    assert read.pending_approval is True


def _queue(factory, token):
    with factory() as db:
        return security.pending_exceptions(token, db)


def test_pending_queue_lists_every_project_core_first(session_factory):
    old, new = NOW - timedelta(days=5), NOW
    with session_factory() as db:
        _finding(db, "shop", "core", Tier.CORE)
        _finding(db, "other", "risk")
        _finding(db, "shop", "plain")
        _finding(db, "shop", "gone")
        _finding(db, "shop", "rev")
        _exception(db, fp="core", created_at=new)
        _exception(db, "other", "risk", status="accepted_risk", created_at=old)
        _exception(db, fp="plain")
        _exception(
            db, fp="gone", status="accepted_risk",
            expires_on=TODAY - timedelta(days=1),
        )  # fmt: skip
        _exception(db, fp="rev", status="accepted_risk", revoked_at=NOW)

    result = _queue(session_factory, AUDITOR)

    assert [(r.project, r.fingerprint) for r in result] == [
        ("shop", "core"),
        ("other", "risk"),
    ]


def test_pending_queue_skips_resolved_findings(session_factory):
    with session_factory() as db:
        _finding(db, fp="fixed")
        store.upsert_findings(db, "shop", [], (Source.CI,), {"web"}, NOW)
        _finding(db, fp="open")
        _exception(db, fp="fixed", status="accepted_risk")
        _exception(db, fp="open", status="accepted_risk")
        _exception(db, fp="unknown", status="accepted_risk")

    result = _queue(session_factory, AUDITOR)

    assert {r.fingerprint for r in result} == {"open", "unknown"}


def test_pending_queue_is_refused_to_a_project_admin(session_factory):
    with pytest.raises(HTTPException) as exc:
        _queue(session_factory, DEV)

    assert exc.value.status_code == 403


def _revoke(factory, token, role, author="other"):
    with factory() as db:
        row_id = _exception(db, author=author, status="accepted_risk").id
    with factory() as db, _role(role), _sync():
        try:
            asyncio.run(security.revoke_exception(row_id, token, db))
        except HTTPException as exc:
            return exc, db.get(SecurityException, row_id)
        return None, db.get(SecurityException, row_id)


def test_a_member_cannot_reactivate_someone_elses_exception(session_factory):
    exc, row = _revoke(
        session_factory, {**OTHER, "preferred_username": "m"}, "member"
    )

    assert exc.status_code == 403
    assert row.revoked_at is None


def test_the_author_reactivates_their_own_exception(session_factory):
    exc, row = _revoke(session_factory, OTHER, "member")

    assert exc is None
    assert row.revoked_at is not None


def test_a_project_admin_reactivates_any_exception(session_factory):
    exc, row = _revoke(
        session_factory, {"sub": "x", "preferred_username": "boss"}, "admin"
    )

    assert exc is None
    assert row.revoked_at is not None


def test_the_auditor_rejects_a_pending_exception(session_factory):
    exc, row = _revoke(session_factory, AUDITOR, None)

    assert exc is None
    assert row.revoked_at is not None


def _create(factory, status, tier, fp="f1"):
    with factory() as db:
        _finding(db, fp=fp, tier=tier)
    payload = rules.ExceptionRequest(
        project="shop",
        status=status,
        justification=(
            "vulnerable_code_not_present"
            if status is rules.Status.NOT_AFFECTED
            else None
        ),
        statement="never called in our code",
    )
    with factory() as db, _role("member"), _sync():
        asyncio.run(
            security.create_exception(
                fp, payload, Request({"type": "http"}), DEV, db
            )
        )


def test_a_core_exception_notifies_platform_admins_and_auditors(
    session_factory,
):
    groups = {
        "infra": {"root", "root@x.io"},
        "security-auditor": {"aud", "dev"},
    }
    with (
        mock.patch.object(
            security, "list_group_usernames", lambda g: groups[g]
        ),
        mock.patch.object(security.notifications, "notify_users") as notify,
    ):
        _create(session_factory, rules.Status.NOT_AFFECTED, Tier.CORE)

    notify.assert_called_once()
    assert set(notify.call_args.args[0]) == {"root", "aud"}
    assert notify.call_args.kwargs == {
        "kind": "approval_request",
        "title_key": "security_exception_pending",
        "params": {"user": "dev", "app": "web"},
        "link": "/security",
        "project": "shop",
        "app": "web",
    }


def test_an_accepted_risk_notifies_the_project_admins(session_factory):
    with (
        mock.patch.object(
            security.notifications,
            "project_recipients",
            lambda p, role: {"boss", "dev"},
        ),
        mock.patch.object(security.notifications, "notify_users") as notify,
    ):
        _create(session_factory, rules.Status.ACCEPTED_RISK, Tier.IMPORTANT)

    assert set(notify.call_args.args[0]) == {"boss"}
    assert notify.call_args.kwargs["link"] == (
        "/security?project=shop&tab=ignored"
    )


def test_an_accepted_risk_without_another_admin_notifies_the_reviewers(
    session_factory,
):
    groups = {"infra": {"root", "dev"}, "security-auditor": {"aud"}}
    with (
        mock.patch.object(
            security, "list_group_usernames", lambda g: groups[g]
        ),
        mock.patch.object(
            security.notifications,
            "project_recipients",
            lambda p, role: {"dev"},
        ),
        mock.patch.object(security.notifications, "notify_users") as notify,
    ):
        _create(session_factory, rules.Status.ACCEPTED_RISK, Tier.IMPORTANT)

    assert set(notify.call_args.args[0]) == {"root", "aud"}


def test_a_keycloak_failure_still_creates_the_exception(session_factory):
    with mock.patch.object(
        security, "list_group_usernames", side_effect=RuntimeError("down")
    ):
        _create(session_factory, rules.Status.NOT_AFFECTED, Tier.CORE)

    with session_factory() as db:
        assert db.query(SecurityException).count() == 1


def test_a_not_affected_exception_notifies_nobody(session_factory):
    with mock.patch.object(security.notifications, "notify_users") as notify:
        _create(session_factory, rules.Status.NOT_AFFECTED, Tier.IMPORTANT)

    notify.assert_not_called()
