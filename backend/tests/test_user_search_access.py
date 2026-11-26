from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.routers import projects
from app.services import keycloak_service
from app.services.keycloak_service import get_current_user

TOKENS = {
    "alice": {"sub": "u-alice", "preferred_username": "alice", "groups": ["infra"]},
    "eve": {"sub": "u-eve", "preferred_username": "eve", "groups": []},
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(projects.router, prefix="/api")
    def user(request: Request):
        return TOKENS[request.headers["x-user"]]

    app.dependency_overrides[get_current_user] = user
    db = mock.Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    app.dependency_overrides[get_db] = lambda: db
    found = mock.Mock(json=lambda: [{"username": "bob", "email": "b@x"}])
    with (
        mock.patch.object(projects, "fetch_user_projects_live", return_value=[]),
        mock.patch.object(keycloak_service, "_get_admin_token", return_value="t"),
        mock.patch.object(projects.requests, "get", return_value=found),
    ):
        yield TestClient(app)


# Neither user administers a project: only infra may search.
@pytest.mark.parametrize("user, code", [("alice", 200), ("eve", 403)])
def test_user_search_without_projects(client, user, code):
    res = client.get("/api/projects/users/search?q=bo", headers={"x-user": user})
    assert res.status_code == code
