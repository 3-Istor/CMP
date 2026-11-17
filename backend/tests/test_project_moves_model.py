import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.project_move import ACTIVE, STEP_NAMES, ProjectMove


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


def test_row_round_trip(db):
    steps = [
        {
            "name": n,
            "status": "pending",
            "detail": None,
            "started_at": None,
            "finished_at": None,
        }
        for n in STEP_NAMES
    ]
    db.add(
        ProjectMove(
            project="alpha",
            source="aws",
            target="gcp",
            status="running",
            steps=steps,
            restores=[],
            actor="u1",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    move = db.query(ProjectMove).one()
    assert move.reason == "button"
    assert move.steps[0]["name"] == "prepare"
    assert move.status in ACTIVE
    assert STEP_NAMES[-1] == "finish"


def _alembic(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_single_alembic_head(tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path}/m.db"}
    assert len(_alembic(env, "heads").strip().splitlines()) == 1
    _alembic(env, "upgrade", "head")
