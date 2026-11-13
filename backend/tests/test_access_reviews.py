import csv
import io
import json
from datetime import datetime, timedelta
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.models.access_review import AccessReviewItem, AccountActivity
from app.models.audit import AuditEvent
from app.models.project import Project
from app.routers import access_reviews
from app.services import audit, grafana_service, keycloak_service
from app.services.access_review import accounts, campaigns, directory
from app.services.keycloak_service import get_current_user

NOW = datetime(2026, 11, 11, 10, 0, 0)
DAY = timedelta(days=1)

TOKENS = {
    "alice": {"preferred_username": "alice", "groups": ["infra"]},
    "bob": {"preferred_username": "bob", "groups": ["/infra"]},
    "olga": {"preferred_username": "olga", "groups": ["member"]},
    "adam": {"preferred_username": "adam", "groups": ["member"]},
    "dan": {"preferred_username": "dan", "groups": ["member"]},
    "sam": {"preferred_username": "sam", "groups": ["security-auditor"]},
}


def millis(value: datetime) -> int:
    return int((value - datetime(1970, 1, 1)).total_seconds() * 1000)


class FakeKeycloak:
    """Realm users, project roles, platform groups, events and sessions."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        for name in ("alice", "bob", "olga", "adam", "dan", "sam"):
            self.add_user(name, created=NOW - 400 * DAY)
        self.projects: dict[str, dict[str, str]] = {
            "shop": {"olga": "admin", "adam": "admin", "dan": "member"},
        }
        self.groups: dict[str, set[str]] = {
            "infra": {"alice", "bob"},
            "security-auditor": {"sam"},
            "finops": set(),
        }
        self.logins: dict[str, datetime] = {}
        self.sessions: dict[str, datetime] = {}
        self.retention = timedelta(days=30)
        self.disabled_calls: list[tuple[str, bool]] = []

    def add_user(self, name, created, **extra):
        self.users[name] = {
            "id": f"id-{name}",
            "username": name,
            "enabled": True,
            "createdTimestamp": millis(created),
            **extra,
        }

    # directory
    def event_retention(self):
        return self.retention

    def login_events(self, since):
        return {
            f"id-{u}": t for u, t in self.logins.items() if t >= since
        }

    def realm_users(self):
        return [dict(u) for u in self.users.values()]

    def last_session_access(self, user_id):
        return self.sessions.get(user_id.removeprefix("id-"))

    def group_members(self, group):
        return sorted(self.groups.get(group, set()))

    def set_enabled(self, user_id, enabled):
        self.disabled_calls.append((user_id, enabled))
        self.users[user_id.removeprefix("id-")]["enabled"] = enabled

    # keycloak_service
    def list_project_members(self, project):
        return [
            {"username": u, "role": r}
            for u, r in sorted(self.projects.get(project, {}).items())
        ]

    def remove_user_from_project(self, username, project):
        if username not in self.users:
            raise ValueError(f"User '{username}' not found in Keycloak.")
        self.projects[project].pop(username, None)

    def add_user_to_project(self, username, project, role):
        self.projects[project][username] = role


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add(Project(project_name="shop", owner_username="olga"))
        db.commit()
    with mock.patch.object(audit, "SessionLocal", session_factory):
        yield session_factory


@pytest.fixture
def keycloak():
    fake = FakeKeycloak()
    with (
        mock.patch.object(directory, "event_retention", fake.event_retention),
        mock.patch.object(directory, "login_events", fake.login_events),
        mock.patch.object(directory, "users", fake.realm_users),
        mock.patch.object(
            directory, "last_session_access", fake.last_session_access
        ),
        mock.patch.object(directory, "group_members", fake.group_members),
        mock.patch.object(directory, "set_enabled", fake.set_enabled),
        mock.patch.object(
            keycloak_service,
            "list_project_members",
            fake.list_project_members,
        ),
        mock.patch.object(
            keycloak_service,
            "remove_user_from_project",
            fake.remove_user_from_project,
        ),
        mock.patch.object(
            keycloak_service,
            "add_user_to_project",
            fake.add_user_to_project,
        ),
        mock.patch.object(
            grafana_service, "add_user_to_project_org", mock.AsyncMock()
        ),
        mock.patch.object(
            grafana_service, "remove_user_from_project_org", mock.AsyncMock()
        ),
    ):
        yield fake


@pytest.fixture
def clock():
    now = {"value": NOW}
    with mock.patch.object(campaigns, "utcnow", lambda: now["value"]):
        yield now


@pytest.fixture
def delivered():
    with mock.patch.object(
        access_reviews, "deliver", mock.AsyncMock()
    ) as fake:
        yield fake


@pytest.fixture
def client(factory, keycloak, clock, delivered):
    app = FastAPI()
    app.add_middleware(audit.AuditMiddleware)
    app.include_router(access_reviews.router, prefix="/api")

    def db():
        with factory() as session:
            yield session

    def user(request: Request):
        return TOKENS[request.headers["x-user"]]

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = user
    with mock.patch.object(
        audit, "_actor", lambda request: request.headers.get("x-user", "")
    ):
        yield TestClient(app)


def as_user(name):
    return {"x-user": name}


def start(client, user="alice"):
    return client.post(
        "/api/access-reviews/campaigns", json={}, headers=as_user(user)
    )


def item_of(factory, username, subject):
    with factory() as db:
        return (
            db.query(AccessReviewItem)
            .filter(
                AccessReviewItem.username == username,
                AccessReviewItem.subject == subject,
            )
            .one()
        )


def decide(client, item_id, user, decision="remove", role=None):
    return client.post(
        f"/api/access-reviews/items/{item_id}/decision",
        json={"decision": decision, "role": role, "note": "left the team"},
        headers=as_user(user),
    )


class TestCampaignStart:
    def test_refuses_to_start_a_campaign_outside_infra(self, client):
        response = start(client, user="sam")

        assert response.status_code == 403

    def test_lists_every_project_member_except_the_owner(
        self, client, factory
    ):
        start(client)

        with factory() as db:
            shop = {
                i.username
                for i in db.query(AccessReviewItem).filter_by(subject="shop")
            }
        assert shop == {"adam", "dan"}

    def test_records_the_last_login_seen_in_keycloak_events(
        self, client, factory, keycloak
    ):
        keycloak.logins["dan"] = NOW - 3 * DAY

        start(client)

        assert item_of(factory, "dan", "shop").last_login_at == NOW - 3 * DAY

    def test_refuses_a_second_open_campaign(self, client):
        start(client)

        response = start(client)

        assert response.status_code == 409

    def test_lets_the_auditor_read_but_not_close_a_campaign(self, client):
        campaign_id = start(client).json()["id"]

        read = client.get(
            f"/api/access-reviews/campaigns/{campaign_id}",
            headers=as_user("sam"),
        )
        closed = client.post(
            f"/api/access-reviews/campaigns/{campaign_id}/close",
            headers=as_user("sam"),
        )

        assert (read.status_code, closed.status_code) == (200, 403)


class TestOwnerOnlyDecisions:
    def test_applies_the_owner_removal_through_the_membership_code(
        self, client, factory, keycloak
    ):
        start(client)
        item = item_of(factory, "dan", "shop")

        response = decide(client, item.id, "olga")

        assert response.json()["outcome"] == "applied"
        assert "dan" not in keycloak.projects["shop"]

    def test_changes_a_role_through_the_membership_code(
        self, client, factory, keycloak
    ):
        start(client)
        item = item_of(factory, "adam", "shop")

        decide(client, item.id, "olga", decision="change_role", role="guest")

        assert keycloak.projects["shop"]["adam"] == "guest"

    def test_refuses_a_project_admin_while_the_owner_is_present(
        self, client, factory, keycloak
    ):
        start(client)
        item = item_of(factory, "dan", "shop")

        response = decide(client, item.id, "adam")

        assert response.status_code == 403
        assert "dan" in keycloak.projects["shop"]

    def test_refuses_infra_on_a_project_the_owner_reviews(
        self, client, factory, keycloak
    ):
        start(client)
        item = item_of(factory, "dan", "shop")

        response = decide(client, item.id, "alice")

        assert response.status_code == 403
        assert "dan" in keycloak.projects["shop"]

    def test_falls_back_to_the_project_admins_when_the_owner_is_gone(
        self, client, factory, keycloak
    ):
        keycloak.users["olga"]["enabled"] = False
        start(client)
        item = item_of(factory, "dan", "shop")

        response = decide(client, item.id, "adam")

        assert response.status_code == 200

    def test_refuses_an_admin_reviewer_demoted_after_the_start(
        self, client, factory, keycloak
    ):
        # Arrange
        keycloak.users["olga"]["enabled"] = False
        start(client)
        item = item_of(factory, "dan", "shop")
        keycloak.projects["shop"]["adam"] = "member"

        # Act
        response = decide(client, item.id, "adam")

        # Assert
        assert response.status_code == 403
        assert "dan" in keycloak.projects["shop"]

    def test_refuses_an_admin_reviewer_removed_after_the_start(
        self, client, factory, keycloak
    ):
        keycloak.users["olga"]["enabled"] = False
        start(client)
        item = item_of(factory, "dan", "shop")
        del keycloak.projects["shop"]["adam"]

        response = decide(client, item.id, "adam", decision="keep")

        assert response.status_code == 403

    def test_lets_infra_decide_an_item_that_fell_back_to_infra(
        self, client, factory, keycloak
    ):
        keycloak.users["olga"]["enabled"] = False
        keycloak.projects["shop"] = {"dan": "member"}
        start(client)
        item = item_of(factory, "dan", "shop")

        response = decide(client, item.id, "alice", decision="keep")

        assert response.status_code == 200

    def test_refuses_an_infra_reviewer_who_left_infra_after_the_start(
        self, client, factory, keycloak
    ):
        # Arrange
        keycloak.users["olga"]["enabled"] = False
        keycloak.projects["shop"] = {"dan": "member"}
        start(client)
        item = item_of(factory, "dan", "shop")

        # Act
        with mock.patch.dict(
            TOKENS, {"bob": {"preferred_username": "bob", "groups": []}}
        ):
            response = decide(client, item.id, "bob", decision="keep")

        # Assert
        assert response.status_code == 403

    def test_refuses_infra_once_a_reviewer_became_project_admin(
        self, client, factory, keycloak
    ):
        keycloak.users["olga"]["enabled"] = False
        keycloak.projects["shop"] = {"dan": "member"}
        start(client)
        item = item_of(factory, "dan", "shop")
        keycloak.projects["shop"]["alice"] = "admin"

        response = decide(client, item.id, "bob", decision="keep")

        assert response.status_code == 403

    def test_refuses_an_admin_decision_when_keycloak_cannot_confirm_the_role(
        self, client, factory, keycloak
    ):
        keycloak.users["olga"]["enabled"] = False
        start(client)
        item = item_of(factory, "dan", "shop")

        with mock.patch.object(
            keycloak_service,
            "list_project_members",
            side_effect=keycloak_service.requests.ConnectionError("down"),
        ):
            response = decide(client, item.id, "adam")

        assert response.status_code == 503
        assert "dan" in keycloak.projects["shop"]

    def test_refuses_an_admin_reviewing_their_own_access(
        self, client, factory, keycloak
    ):
        keycloak.users["olga"]["enabled"] = False
        start(client)
        item = item_of(factory, "adam", "shop")

        response = decide(client, item.id, "adam", decision="keep")

        assert response.status_code == 403

    def test_refuses_a_second_decision_on_the_same_item(
        self, client, factory
    ):
        start(client)
        item = item_of(factory, "dan", "shop")
        decide(client, item.id, "olga", decision="keep")

        response = decide(client, item.id, "olga")

        assert response.status_code == 409

    def test_leaves_the_item_undecided_when_keycloak_refuses(
        self, client, factory, keycloak
    ):
        start(client)
        item = item_of(factory, "dan", "shop")

        with mock.patch.object(
            keycloak_service,
            "remove_user_from_project",
            side_effect=keycloak_service.requests.ConnectionError("down"),
        ):
            response = decide(client, item.id, "olga")

        assert response.status_code == 502
        assert item_of(factory, "dan", "shop").decision is None

    def test_shows_the_owner_their_pending_items_for_the_project_card(
        self, client
    ):
        start(client)

        response = client.get(
            "/api/access-reviews/mine?project=shop", headers=as_user("olga")
        )

        assert [i["username"] for i in response.json()["items"]] == [
            "adam",
            "dan",
        ]


class TestFourEyesOnPlatformGroups:
    def test_refuses_an_infra_member_reviewing_their_own_access(
        self, client, factory
    ):
        start(client)
        item = item_of(factory, "alice", "infra")

        response = decide(client, item.id, "alice", decision="keep")

        assert response.status_code == 403

    def test_lets_another_infra_member_decide(self, client, factory):
        start(client)
        item = item_of(factory, "alice", "infra")

        response = decide(client, item.id, "bob", decision="keep")

        assert response.json()["outcome"] == "unchanged"

    def test_refuses_a_platform_decision_from_outside_infra(
        self, client, factory
    ):
        start(client)
        item = item_of(factory, "sam", "security-auditor")

        response = decide(client, item.id, "olga")

        assert response.status_code == 403

    def test_turns_a_removal_into_a_terraform_task_without_touching_keycloak(
        self, client, factory, keycloak
    ):
        start(client)
        item = item_of(factory, "sam", "security-auditor")

        response = decide(client, item.id, "alice")

        assert response.json()["outcome"] == "terraform_task"
        assert 'platform_group_members["security-auditor"]' in (
            response.json()["outcome_detail"]
        )
        assert "sam" in keycloak.groups["security-auditor"]

    def test_refuses_a_role_change_on_a_platform_group(self, client, factory):
        start(client)
        item = item_of(factory, "sam", "security-auditor")

        response = decide(
            client, item.id, "alice", decision="change_role", role="guest"
        )

        assert response.status_code == 422


class TestDeadline:
    def test_escalates_undecided_items_without_removing_anyone(
        self, client, factory, keycloak
    ):
        start(client)

        with factory() as db:
            notices = campaigns.escalate(db, NOW + 15 * DAY)

        assert [n.kind for n in notices] == ["escalated"]
        assert keycloak.projects["shop"] == {
            "olga": "admin",
            "adam": "admin",
            "dan": "member",
        }
        assert item_of(factory, "dan", "shop").decision is None

    def test_escalates_a_campaign_once(self, client, factory):
        start(client)

        with factory() as db:
            campaigns.escalate(db, NOW + 15 * DAY)
            again = campaigns.escalate(db, NOW + 16 * DAY)

        assert again == []

    def test_does_not_escalate_before_the_deadline(self, client, factory):
        start(client)

        with factory() as db:
            notices = campaigns.escalate(db, NOW + 13 * DAY)

        assert notices == []


class TestScheduledStart:
    def test_opens_a_campaign_once_the_interval_has_passed(
        self, factory, keycloak
    ):
        with (
            factory() as db,
            mock.patch.object(settings, "ACCESS_REVIEW_AUTO_START", True),
        ):
            first = campaigns.maybe_auto_start(db, NOW)
            campaigns.close(db, 1, "alice", NOW + DAY)
            too_soon = campaigns.maybe_auto_start(db, NOW + 30 * DAY)
            due = campaigns.maybe_auto_start(db, NOW + 90 * DAY)

        assert (bool(first), too_soon, bool(due)) == (True, [], True)

    def test_does_nothing_when_auto_start_is_off(self, factory, keycloak):
        with factory() as db:
            notices = campaigns.maybe_auto_start(db, NOW)

        assert notices == []


class TestEvidence:
    def test_exports_who_decided_what_and_when_as_csv(self, client, factory):
        campaign_id = start(client).json()["id"]
        decide(client, item_of(factory, "dan", "shop").id, "olga")

        response = client.get(
            f"/api/access-reviews/campaigns/{campaign_id}/export?format=csv",
            headers=as_user("sam"),
        )

        rows = list(csv.DictReader(io.StringIO(response.text)))
        dan = next(r for r in rows if r["username"] == "dan")
        assert (dan["decision"], dan["decided_by"], dan["decided_at"]) == (
            "remove",
            "olga",
            "2026-11-11T10:00:00Z",
        )

    def test_exports_the_campaign_as_json(self, client):
        campaign_id = start(client).json()["id"]

        response = client.get(
            f"/api/access-reviews/campaigns/{campaign_id}/export?format=json",
            headers=as_user("alice"),
        )

        assert response.json()["campaign"]["id"] == campaign_id

    def test_neutralises_spreadsheet_formulas_in_the_csv(
        self, client, factory
    ):
        campaign_id = start(client).json()["id"]
        client.post(
            f"/api/access-reviews/items/"
            f"{item_of(factory, 'dan', 'shop').id}/decision",
            json={"decision": "keep", "note": "=HYPERLINK(1)"},
            headers=as_user("olga"),
        )

        response = client.get(
            f"/api/access-reviews/campaigns/{campaign_id}/export",
            headers=as_user("alice"),
        )

        assert "'=HYPERLINK(1)" in response.text

    def test_journals_the_decision_in_audit_events(self, client, factory):
        start(client)
        decide(client, item_of(factory, "dan", "shop").id, "olga")

        with factory() as db:
            event = (
                db.query(AuditEvent)
                .filter(AuditEvent.action == "access_review.decide")
                .one()
            )
        assert (event.actor, event.project, event.target) == (
            "olga",
            "shop",
            "dan",
        )
        assert json.loads(event.details)["decision"] == "remove"


def inactivity(factory, now):
    with factory() as db:
        return accounts.check_inactive(db, now)


@pytest.fixture
def tracked(factory, keycloak):
    """Every user seen once, long ago, so the event store covers them."""
    with factory() as db:
        accounts.refresh_logins(db, NOW - 200 * DAY)
    return keycloak


class TestInactiveAccounts:
    def test_never_reports_demo_accounts(self, factory, tracked):
        tracked.add_user("demo-owner", created=NOW - 400 * DAY)

        notices = inactivity(factory, NOW)

        assert "demo-owner" not in {n.fields[0][1] for n in notices}

    def test_never_disables_demo_accounts(self, factory, tracked):
        tracked.add_user("demo-owner", created=NOW - 400 * DAY)

        with mock.patch.object(settings, "INACTIVE_DISABLE_ENABLED", True):
            inactivity(factory, NOW)
            inactivity(factory, NOW + 15 * DAY)

        assert tracked.users["demo-owner"]["enabled"] is True

    def test_never_reports_break_glass_accounts(self, factory, tracked):
        tracked.add_user("break-glass-admin", created=NOW - 400 * DAY)

        notices = inactivity(factory, NOW)

        assert "break-glass-admin" not in {n.fields[0][1] for n in notices}

    def test_reports_an_account_without_login_for_sixty_days(
        self, factory, tracked
    ):
        tracked.logins = {
            u: NOW - DAY for u in tracked.users if u != "dan"
        }

        notices = inactivity(factory, NOW)

        assert [n.fields[0][1] for n in notices] == ["dan"]

    def test_does_not_report_an_account_with_a_live_session(
        self, factory, tracked
    ):
        tracked.logins = {u: NOW - DAY for u in tracked.users if u != "dan"}
        tracked.sessions["dan"] = NOW - 2 * DAY

        notices = inactivity(factory, NOW)

        assert notices == []

    def test_disables_after_the_grace_period_when_enabled(
        self, factory, tracked
    ):
        tracked.logins = {u: NOW - DAY for u in tracked.users if u != "dan"}

        with mock.patch.object(settings, "INACTIVE_DISABLE_ENABLED", True):
            inactivity(factory, NOW)
            early = inactivity(factory, NOW + 13 * DAY)
            late = inactivity(factory, NOW + 14 * DAY)

        assert (early, [n.kind for n in late]) == ([], ["disabled"])
        assert tracked.users["dan"]["enabled"] is False

    def test_never_disables_while_the_setting_is_off(self, factory, tracked):
        tracked.logins = {u: NOW - DAY for u in tracked.users if u != "dan"}

        inactivity(factory, NOW)
        inactivity(factory, NOW + 30 * DAY)

        assert tracked.disabled_calls == []

    def test_reports_nobody_before_the_event_store_covers_sixty_days(
        self, factory, keycloak
    ):
        notices = inactivity(factory, NOW)

        assert notices == []

    def test_enabling_a_disabled_account_restarts_its_clock(
        self, client, factory, tracked
    ):
        tracked.logins = {u: NOW - DAY for u in tracked.users if u != "dan"}
        with mock.patch.object(settings, "INACTIVE_DISABLE_ENABLED", True):
            inactivity(factory, NOW)
            inactivity(factory, NOW + 14 * DAY)

        response = client.post(
            "/api/access-reviews/accounts/id-dan/enable",
            headers=as_user("alice"),
        )

        assert response.status_code == 200
        assert tracked.users["dan"]["enabled"] is True
        with factory() as db:
            assert db.get(AccountActivity, "id-dan").tracked_since == NOW
