from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.routers import account
from app.services.imports.access import RepoRef
from app.services.keycloak_service import get_current_user

TOKEN = {"preferred_username": "alice", "sub": "uid-alice"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(account.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: TOKEN
    return TestClient(app)


def test_my_repos_list_the_callers_importable_repositories(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "IMPORT_ENABLED", True)
    repo = RepoRef("3-Istor/epitweet", 7, 42, "main", True)
    with mock.patch.object(
        account.access,
        "importable_repositories",
        new=mock.AsyncMock(return_value=[repo]),
    ) as listing:
        response = client.get("/api/account/github/repos")

    assert response.status_code == 200
    assert response.json() == {
        "repositories": [
            {
                "full_name": "3-Istor/epitweet",
                "default_branch": "main",
                "private": True,
            }
        ]
    }
    assert listing.await_args.args[1] == TOKEN


def test_my_repos_404_when_import_is_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_ENABLED", False)

    response = client.get("/api/account/github/repos")

    assert response.status_code == 404
