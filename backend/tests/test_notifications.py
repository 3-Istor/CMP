import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import mcp_server
from app.core.database import Base
from app.core.roles import ProjectRole
from app.models.notification import Notification
from app.models.project import Project
from app.routers import notifications as router
from app.services import notifications
from app.services.agents import policy
from app.services.notifications import utcnow

MEMBERS = [
    {"username": "ada", "role": "admin"},
    {"username": "bob", "role": "member"},
    {"username": "gus", "role": "guest"},
]


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    return factory


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def members(monkeypatch):
    monkeypatch.setattr(
        notifications.keycloak_service,
        "list_project_members",
        lambda project: MEMBERS if project == "shop" else [],
    )


def _claims(username):
    return {"sub": f"sub-{username}", "preferred_username": username}


def _recipients(db, **filters):
    rows = db.query(Notification).filter_by(**filters).all()
    return {row.user_key for row in rows}


class TestRecipients:
    def test_project_notification_reaches_every_member_and_the_owner(
        self, db, members
    ):
        db.add(Project(project_name="shop", owner_username="olga"))
        db.commit()

        notifications.notify_project(
            "shop", kind="alert", title_key="app_degraded"
        )

        assert _recipients(db) == {"ada", "bob", "gus", "olga"}

    def test_project_notification_never_reaches_a_user_outside_the_project(
        self, db, members
    ):
        notifications.notify_project(
            "shop", kind="alert", title_key="app_degraded"
        )

        assert "mallory" not in _recipients(db)

    def test_admin_only_notification_skips_members_and_guests(
        self, db, members
    ):
        db.add(Project(project_name="shop", owner_username="olga"))
        db.commit()

        notifications.notify_project(
            "shop",
            kind="alert",
            title_key="budget_critical",
            minimum=ProjectRole.ADMIN,
        )

        assert _recipients(db) == {"ada", "olga"}

    def test_nothing_is_stored_when_the_member_lookup_fails(
        self, db, monkeypatch
    ):
        def boom(project):
            raise RuntimeError("keycloak down")

        monkeypatch.setattr(
            notifications.keycloak_service, "list_project_members", boom
        )

        written = notifications.notify_project(
            "shop", kind="alert", title_key="app_degraded"
        )

        assert written == 0
        assert db.query(Notification).count() == 0

    def test_added_member_is_the_only_recipient(self, db):
        notifications.notify_users(
            ["bob"],
            kind="member_added",
            title_key="member_added",
            project="shop",
        )

        assert _recipients(db) == {"bob"}


class TestApprovalRequests:
    def test_approval_request_goes_to_project_admins_only(
        self, db, members, monkeypatch
    ):
        monkeypatch.setattr(policy, "project_stake", lambda project: "high")
        monkeypatch.setattr(policy, "rules", lambda: {})
        monkeypatch.setattr(
            policy,
            "decide",
            lambda *args: SimpleNamespace(
                outcome="confirm", reason="r", level="high"
            ),
        )
        monkeypatch.setattr(
            policy,
            "request_confirmation",
            lambda *args: SimpleNamespace(id="abc", expires_at=datetime.now()),
        )
        caller = SimpleNamespace(username="bob", grant_id=1)

        verdict, answer = asyncio.run(
            mcp_server._gate(  # pylint: disable=protected-access
                caller,
                "deploy",
                "shop",
                tool="t",
                arguments={},
                summary="Deploy it",
            )
        )

        assert verdict == "stop"
        assert answer["status"] == "needs_cmp_approval"
        assert _recipients(db, kind="approval_request") == {"ada"}


class TestEndpoints:
    def _add(self, db, user, **fields):
        row = Notification(
            user_key=user,
            kind="alert",
            title_key="app_degraded",
            created_at=utcnow(),
            **fields,
        )
        db.add(row)
        db.commit()
        return row

    def test_list_returns_only_the_callers_notifications(self, db):
        self._add(db, "ada")
        self._add(db, "bob")

        result = router.list_notifications(
            _claims("ada"), unread=False, limit=30, before=None, db=db
        )

        assert [r.user_key for r in result] == ["ada"]

    def test_unread_count_ignores_other_users_and_read_entries(self, db):
        self._add(db, "ada")
        self._add(db, "ada", read_at=utcnow())
        self._add(db, "bob")

        assert router.unread_count(_claims("ada"), db) == {"count": 1}

    def test_marking_another_users_notification_read_is_a_404(self, db):
        other = self._add(db, "bob")

        with pytest.raises(HTTPException) as err:
            router.mark_read(other.id, _claims("ada"), db)

        assert err.value.status_code == 404
        assert db.get(Notification, other.id).read_at is None

    def test_mark_read_sets_read_at_for_the_owner(self, db):
        mine = self._add(db, "ada")

        router.mark_read(mine.id, _claims("ada"), db)

        assert db.get(Notification, mine.id).read_at is not None

    def test_read_all_leaves_other_users_unread(self, db):
        self._add(db, "ada")
        theirs = self._add(db, "bob")

        result = router.mark_all_read(_claims("ada"), db)

        assert result == {"updated": 1}
        assert db.get(Notification, theirs.id).read_at is None

    def test_unread_filter_hides_read_entries(self, db):
        self._add(db, "ada", read_at=utcnow())
        unread = self._add(db, "ada")

        result = router.list_notifications(
            _claims("ada"), unread=True, limit=30, before=None, db=db
        )

        assert [r.id for r in result] == [unread.id]

    def test_before_cursor_returns_older_entries_newest_first(self, db):
        first = self._add(db, "ada")
        second = self._add(db, "ada")
        third = self._add(db, "ada")

        result = router.list_notifications(
            _claims("ada"), unread=False, limit=30, before=third.id, db=db
        )

        assert [r.id for r in result] == [second.id, first.id]

    def test_limit_caps_the_page(self, db):
        for _ in range(3):
            self._add(db, "ada")

        result = router.list_notifications(
            _claims("ada"), unread=False, limit=2, before=None, db=db
        )

        assert len(result) == 2

    def test_timestamps_are_serialised_as_utc(self, db):
        row = self._add(db, "ada")

        dumped = router.NotificationRead.model_validate(row).model_dump(
            mode="json"
        )

        assert dumped["created_at"].endswith("Z")


class TestRetention:
    def _add(self, db, age_days, read):
        now = utcnow()
        row = Notification(
            user_key="ada",
            kind="alert",
            title_key="app_degraded",
            created_at=now - timedelta(days=age_days),
            read_at=now if read else None,
        )
        db.add(row)
        db.commit()
        return row.id

    def test_read_entries_older_than_30_days_are_purged(self, db):
        old_read = self._add(db, 31, read=True)
        recent_read = self._add(db, 5, read=True)

        notifications.purge_expired()

        assert db.get(Notification, old_read) is None
        assert db.get(Notification, recent_read) is not None

    def test_unread_entries_survive_until_90_days(self, db):
        unread_60 = self._add(db, 60, read=False)
        unread_91 = self._add(db, 91, read=False)

        notifications.purge_expired()

        assert db.get(Notification, unread_60) is not None
        assert db.get(Notification, unread_91) is None
