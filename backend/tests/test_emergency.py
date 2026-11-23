from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.project_move import ProjectMove
from app.routers import emergency as router
from app.services import emergency
from app.services.keycloak_service import get_current_user


def _rec(name, cloud, apps=(), protected=False):
    spec = {"targetCloud": cloud, "apps": [{"name": a} for a in apps]}
    if protected:
        spec["deletionProtection"] = True
    return {"metadata": {"name": name}, "spec": spec}


def _move(project, status="running", reason="button", day=1):
    return ProjectMove(
        id=day,
        project=project,
        source="aws",
        target="gcp",
        reason=reason,
        status=status,
        steps=[
            {"name": "prepare", "status": "done"},
            {"name": "stop_source", "status": "running"},
        ],
        created_at=datetime(2026, 11, day, tzinfo=timezone.utc),
    )


def test_projects_sorted_favorites_then_down():
    states = {"onprem": "up", "aws": "degraded", "gcp": "down"}
    records = [
        _rec("fine", "onprem"),
        _rec("a-plain", "aws"),
        _rec("b-fav", "gcp"),
        _rec("c-prot", "gcp", protected=True),
        _rec("d-plain", "gcp"),
    ]
    body = emergency.build(states, records, [], {"b-fav"})
    assert [p["name"] for p in body["projects"]] == [
        "b-fav",
        "c-prot",
        "d-plain",
        "a-plain",
    ]
    assert body["projects"][0]["favorite"] is True
    assert body["projects"][1]["protected"] is True


def test_moves_active_and_auto_first_with_current_step():
    moves = [
        _move("old", status="done", day=3),
        _move("manual", day=1),
        _move("broken", status="failed", day=4),
        _move("auto", reason="outage", day=2),
    ]
    rows = emergency.build({}, [], moves, set())["moves"]
    assert [m["project"] for m in rows] == ["broken", "auto", "manual", "old"]
    assert rows[1]["auto"] is True and rows[2]["auto"] is False
    assert rows[1]["step"] == "stop_source"


def test_requires_platform_admin():
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    assert TestClient(app).get("/api/emergency").status_code == 403
