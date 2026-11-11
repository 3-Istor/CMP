from datetime import datetime, timedelta
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.access import AccessRequest
from app.models.audit import AuditEvent
from app.routers import access
from app.services import audit
from app.services.access import grants, groups
from app.services.keycloak_service import get_current_user

TOKENS = {
    "alice": {"sub": "a", "preferred_username": "alice", "groups": ["infra"]},
    "bob": {"sub": "b", "preferred_username": "bob", "groups": ["/infra"]},
    "eve": {"sub": "e", "preferred_username": "eve", "groups": ["member"]},
}
VAULT_GROUP = "elevated-vault-data"
NOW = datetime(2026, 11, 11, 10, 0, 0)


class FakeKeycloak:
    def __init__(self):
        self.members: dict[str, set[str]] = {
            "elevated-vault-data": set(),
            "elevated-k3s-admin": set(),
        }
        self.fail = False

    def _check(self):
        if self.fail:
            raise groups.GroupError("keycloak down")

    def add_member(self, username, group):
        self._check()
        self.members[group].add(username)

    def remove_member(self, username, group):
        self._check()
        self.members[group].discard(username)

    def list_members(self, group):
        self._check()
        return set(self.members[group])


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with mock.patch.object(audit, "SessionLocal", session_factory):
        yield session_factory


@pytest.fixture
def keycloak():
    fake = FakeKeycloak()
    with (
        mock.patch.object(groups, "add_member", fake.add_member),
        mock.patch.object(groups, "remove_member", fake.remove_member),
        mock.patch.object(groups, "members", fake.list_members),
    ):
        yield fake


@pytest.fixture
def clock():
    now = {"value": NOW}
    with mock.patch.object(grants, "utcnow", lambda: now["value"]):
        yield now


@pytest.fixture
def announced():
    with mock.patch.object(grants, "announce", mock.AsyncMock()) as fake:
        yield fake


@pytest.fixture
def client(factory, keycloak, clock, announced):
    app = FastAPI()
    app.include_router(access.router, prefix="/api")

    def db():
        with factory() as session:
            yield session

    def user(request: Request):
        return TOKENS[request.headers["x-user"]]

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = user
    return TestClient(app)


def as_user(name):
    return {"x-user": name}


def ask(client, user="alice", elevation="vault-data", minutes=60):
    return client.post(
        "/api/access/requests",
        json={
            "elevation": elevation,
            "reason": "Rotate the payments database password",
            "duration_minutes": minutes,
        },
        headers=as_user(user),
    )


def sweep(factory):
    with factory() as db:
        return grants.sweep(db)


class TestPlatformAdminsOnly:
    def test_refuses_the_overview_to_a_user_outside_infra(self, client):
        response = client.get("/api/access", headers=as_user("eve"))

        assert response.status_code == 403

    def test_refuses_a_request_from_a_user_outside_infra(
        self, client, factory
    ):
        response = ask(client, user="eve")

        assert response.status_code == 403
        with factory() as db:
            assert db.query(AccessRequest).count() == 0

    def test_refuses_an_approval_from_a_user_outside_infra(
        self, client, keycloak
    ):
        request_id = ask(client).json()["id"]

        response = client.post(
            f"/api/access/requests/{request_id}/approve",
            headers=as_user("eve"),
        )

        assert response.status_code == 403
        assert keycloak.members[VAULT_GROUP] == set()


class TestFourEyes:
    def test_refuses_an_approval_by_the_requester(
        self, client, keycloak, factory
    ):
        request_id = ask(client).json()["id"]

        response = client.post(
            f"/api/access/requests/{request_id}/approve",
            headers=as_user("alice"),
        )

        assert response.status_code == 403
        assert keycloak.members[VAULT_GROUP] == set()
        with factory() as db:
            assert db.get(AccessRequest, request_id).status == "pending"

    def test_adds_the_requester_to_the_group_when_another_admin_approves(
        self, client, keycloak
    ):
        request_id = ask(client, minutes=30).json()["id"]

        response = client.post(
            f"/api/access/requests/{request_id}/approve",
            headers=as_user("bob"),
        )

        assert response.status_code == 200
        body = response.json()
        assert (body["status"], body["decided_by"]) == ("active", "bob")
        assert body["expires_at"] == "2026-11-11T10:30:00Z"
        assert keycloak.members[VAULT_GROUP] == {"alice"}

    def test_refuses_a_second_decision_on_the_same_request(self, client):
        request_id = ask(client).json()["id"]
        client.post(
            f"/api/access/requests/{request_id}/deny",
            json={"reason": "not now"},
            headers=as_user("bob"),
        )

        response = client.post(
            f"/api/access/requests/{request_id}/approve",
            headers=as_user("bob"),
        )

        assert response.status_code == 409

    def test_marks_the_grant_failed_when_keycloak_refuses(
        self, client, keycloak, factory
    ):
        request_id = ask(client).json()["id"]
        keycloak.fail = True

        response = client.post(
            f"/api/access/requests/{request_id}/approve",
            headers=as_user("bob"),
        )

        assert response.status_code == 502
        with factory() as db:
            assert db.get(AccessRequest, request_id).status == "failed"


class TestRequestValidation:
    def test_refuses_a_duration_above_the_configured_maximum(self, client):
        response = ask(client, minutes=120)

        assert response.status_code == 422

    def test_refuses_an_unknown_elevation(self, client):
        response = ask(client, elevation="root-everything")

        assert response.status_code == 422

    def test_refuses_a_second_open_request_for_the_same_elevation(
        self, client
    ):
        ask(client)

        response = ask(client)

        assert response.status_code == 409

    def test_announces_a_new_request(self, client, announced):
        ask(client)

        (items,) = announced.await_args.args
        assert [(a.kind, a.requester) for a in items] == [
            ("requested", "alice")
        ]


class TestExpiry:
    def approved(self, client, minutes=60):
        request_id = ask(client, minutes=minutes).json()["id"]
        client.post(
            f"/api/access/requests/{request_id}/approve",
            headers=as_user("bob"),
        )
        return request_id

    def test_removes_the_membership_once_the_grant_expires(
        self, client, keycloak, factory, clock
    ):
        request_id = self.approved(client)
        clock["value"] = NOW + timedelta(minutes=61)

        sweep(factory)

        assert keycloak.members[VAULT_GROUP] == set()
        with factory() as db:
            row = db.get(AccessRequest, request_id)
            assert (row.status, row.ended_by) == ("expired", "cmp")
            assert (
                db.query(AuditEvent)
                .filter(AuditEvent.action == "access.expire")
                .count()
                == 1
            )

    def test_keeps_the_grant_before_its_end(
        self, client, keycloak, factory, clock
    ):
        self.approved(client)
        clock["value"] = NOW + timedelta(minutes=59)

        sweep(factory)

        assert keycloak.members[VAULT_GROUP] == {"alice"}

    def test_retries_when_keycloak_cannot_remove_the_member(
        self, client, keycloak, factory, clock
    ):
        request_id = self.approved(client)
        clock["value"] = NOW + timedelta(minutes=61)
        keycloak.fail = True

        sweep(factory)

        with factory() as db:
            assert db.get(AccessRequest, request_id).status == "active"

    def test_requester_can_end_the_grant_early(
        self, client, keycloak, factory
    ):
        request_id = self.approved(client)

        response = client.post(
            f"/api/access/requests/{request_id}/end",
            headers=as_user("alice"),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "revoked"
        assert keycloak.members[VAULT_GROUP] == set()

    def test_lapses_a_request_nobody_decided_on(self, client, factory, clock):
        request_id = ask(client).json()["id"]
        clock["value"] = NOW + timedelta(hours=3)

        sweep(factory)

        with factory() as db:
            assert db.get(AccessRequest, request_id).status == "lapsed"


class TestMembersAddedOutsideTheCmp:
    def test_removes_and_reports_a_member_without_a_grant(
        self, client, keycloak, factory
    ):
        keycloak.members["elevated-k3s-admin"].add("mallory")

        items = sweep(factory)

        assert keycloak.members["elevated-k3s-admin"] == set()
        assert [(a.kind, a.requester) for a in items] == [
            ("unmanaged", "mallory")
        ]

    def test_leaves_a_member_holding_an_active_grant(
        self, client, keycloak, factory
    ):
        request_id = ask(client).json()["id"]
        client.post(
            f"/api/access/requests/{request_id}/approve",
            headers=as_user("bob"),
        )

        items = sweep(factory)

        assert keycloak.members[VAULT_GROUP] == {"alice"}
        assert items == []


class TestGroupGuard:
    @pytest.mark.parametrize("group", ["infra", "project-shop-admins"])
    def test_refuses_to_touch_a_group_outside_the_catalogue(self, group):
        with mock.patch.object(groups.requests, "request") as http:
            with pytest.raises(groups.GroupError):
                groups.add_member("alice", group)

        http.assert_not_called()


class TestNotifications:
    def test_asks_every_other_platform_admin_to_approve(self):
        item = grants.Announcement(
            kind="requested",
            request_id=1,
            elevation="vault-data",
            requester="alice",
            actor="alice",
        )

        with (
            mock.patch.object(
                grants.keycloak_service,
                "list_group_usernames",
                return_value={"alice", "bob", "bob@example.org"},
            ),
            mock.patch.object(grants.notifications, "notify_users") as notify,
        ):
            grants._notify(item)  # pylint: disable=protected-access

        assert notify.call_args.args[0] == {"bob"}
