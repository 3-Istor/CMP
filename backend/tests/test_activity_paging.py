import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic.migration import MigrationContext
from alembic.operations import Operations
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


def add_cmp(session_factory, stamp: str):
    # The way the old server default stored it: seconds, no fraction.
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO audit_events (created_at, actor, action, outcome,"
                " status_code, target, source_ip, details) VALUES (:t, 'bob',"
                " 'app.config.update', 'success', 200, '', '', '{}')"
            ),
            {"t": stamp},
        )
        db.commit()


def walk(client, **extra) -> list[str]:
    """Every event id, page by page with limit 2."""
    params = {
        "limit": 2,
        "since": (T0 - timedelta(days=1)).isoformat(),
        "until": (T0 + timedelta(days=1)).isoformat(),
        **extra,
    }
    seen = []
    page = client.get("/api/activity", params=params).json()["events"]
    for _ in range(20):
        if not page:
            break
        seen += [e["id"] for e in page]
        last = page[-1]
        page = client.get(
            "/api/activity",
            params={**params, "until": last["time"], "before_id": last["id"]},
        ).json()["events"]
    assert not page, "paging did not finish"
    return seen


def test_activity_paging_keeps_events_sharing_a_timestamp(
    client, session_factory
):
    add(session_factory, "a", T0)
    for name in ("b", "c", "d"):
        add(session_factory, name, T0 - timedelta(minutes=1))
    add(session_factory, "e", T0 - timedelta(minutes=2))

    assert sorted(walk(client)) == ["a", "b", "c", "d", "e"]


def migrate_fractions(session_factory):
    path = Path(__file__).parent.parent / "alembic/versions"
    spec = importlib.util.spec_from_file_location(
        "fraction_migration", next(path.glob("e7b3d1a9c5f2_*.py"))
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with session_factory() as db:
        conn = db.connection()
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        db.commit()


def test_activity_paging_journal_rows_stored_without_a_fraction(
    client, session_factory
):
    # Rows written by the old server default, then normalised by the migration.
    for stamp in ("2026-11-01 12:00:00",) * 3 + ("2026-11-01 11:59:59",) * 2:
        add_cmp(session_factory, stamp)
    migrate_fractions(session_factory)

    seen = walk(client)

    assert sorted(seen) == [f"cmp-{i}" for i in range(1, 6)]
    assert len(seen) == 5


def test_activity_paging_across_sources_at_the_same_instant(
    client, session_factory
):
    stamp = T0.strftime("%Y-%m-%d %H:%M:%S.000000")
    add_cmp(session_factory, stamp)
    add_cmp(session_factory, stamp)
    for name in ("r1", "r2", "r3"):
        add(session_factory, name, T0)

    seen = walk(client)

    assert sorted(seen) == ["cmp-1", "cmp-2", "r1", "r2", "r3"]
    assert len(seen) == 5


def test_activity_paging_with_notable_filter_and_ties(client, session_factory):
    for i in range(4):
        add(session_factory, f"plain-{i}", T0)
    for name in ("n1", "n2", "n3"):
        add(session_factory, name, T0 - timedelta(hours=1), notable=True)

    seen = walk(client, notable_only=True)

    assert sorted(seen) == ["n1", "n2", "n3"]


@pytest.mark.parametrize("bad", ["cmp-٣", "cmp-" + "9" * 40, "cmp-", "cmp-1x"])
def test_activity_before_id_malformed_cmp_cursor_is_422(client, bad):
    r = client.get("/api/activity", params={"before_id": bad})

    assert r.status_code == 422


def test_activity_before_id_overlong_is_422(client):
    r = client.get("/api/activity", params={"before_id": "x" * 300})

    assert r.status_code == 422
