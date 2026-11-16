from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.audit import ActivityRecord
from app.routers import activity as activity_router
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import session_factory  # noqa: F401

T0 = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def client(session_factory):
    app = FastAPI()
    app.include_router(activity_router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "u",
        "preferred_username": "alice",
        "groups": ["/infra"],
    }
    return TestClient(app)


def add(session_factory, name: str, when: datetime, notable=False):
    with session_factory() as db:
        db.add(
            ActivityRecord(
                external_id=name,
                source="vault",
                time=when,
                actor="bob",
                action="x",
                notable=notable,
                outcome="success",
            )
        )
        db.commit()


def test_activity_page_is_full_when_more_events_exist(client, session_factory):
    # Newest rows are filtered out; the notable ones sit behind them.
    for i in range(6):
        add(session_factory, f"plain-{i}", T0 - timedelta(minutes=i))
    for i in range(4):
        add(
            session_factory,
            f"notable-{i}",
            T0 - timedelta(hours=1, minutes=i),
            notable=True,
        )

    r = client.get(
        "/api/activity",
        params={
            "notable_only": True,
            "limit": 3,
            "since": (T0 - timedelta(days=1)).isoformat(),
            "until": (T0 + timedelta(days=1)).isoformat(),
        },
    )

    assert len(r.json()["events"]) == 3


def test_activity_paging_keeps_events_sharing_a_timestamp(
    client, session_factory
):
    add(session_factory, "a", T0)
    for name in ("b", "c", "d"):
        add(session_factory, name, T0 - timedelta(minutes=1))
    add(session_factory, "e", T0 - timedelta(minutes=2))
    params = {
        "limit": 2,
        "since": (T0 - timedelta(days=1)).isoformat(),
        "until": (T0 + timedelta(days=1)).isoformat(),
    }

    seen = []
    page = client.get("/api/activity", params=params).json()["events"]
    while page:
        seen += [e["id"] for e in page]
        last = page[-1]
        page = client.get(
            "/api/activity",
            params={**params, "until": last["time"], "before_id": last["id"]},
        ).json()["events"]

    assert sorted(seen) == ["a", "b", "c", "d", "e"]
