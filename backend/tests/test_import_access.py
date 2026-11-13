import asyncio

import pytest

from app.services import github_service
from app.services.imports import access
from app.services.imports.access import RepoRef

PLATFORM_ID = 900
RENAMED_PLATFORM = RepoRef("3-Istor/not-k3s", PLATFORM_ID, 7, "main", True)
USER_REPO = RepoRef("alice/shop", 42, 7, "main", True)


@pytest.fixture
def linked(monkeypatch):
    async def ids():
        return frozenset({PLATFORM_ID})

    async def repos(db, claims):
        return [RENAMED_PLATFORM, USER_REPO]

    monkeypatch.setattr(access, "denied_repo_ids", ids)
    monkeypatch.setattr(access, "linked_repositories", repos)


def test_renamed_platform_repository_is_still_refused(linked):
    repos = asyncio.run(access.importable_repositories(None, {}))

    assert repos == [USER_REPO]


def test_repository_is_resolved_by_id_when_one_is_bound(linked):
    repo = asyncio.run(
        access.resolve_repository(None, {}, "alice/old-name", repo_id=42)
    )

    assert repo == USER_REPO


def test_deny_list_ids_are_resolved_once_and_cached(monkeypatch):
    calls = []

    async def resolve(names):
        calls.append(names)
        return frozenset({PLATFORM_ID})

    monkeypatch.setattr(access, "_resolve_ids", resolve)
    monkeypatch.setattr(access, "_deny_cache", {})

    asyncio.run(access.denied_repo_ids())
    ids = asyncio.run(access.denied_repo_ids())

    assert ids == frozenset({PLATFORM_ID})
    assert len(calls) == 1


def test_repository_token_is_scoped_by_id(monkeypatch):
    sent = {}

    async def request(installation_id, body):
        sent.update(body)
        return "ghs_x"

    monkeypatch.setattr(github_service, "_request_installation_token", request)

    asyncio.run(
        github_service.get_repository_id_scoped_token(
            "7", [42], {"contents": "read"}
        )
    )

    assert sent == {
        "repository_ids": [42],
        "permissions": {"contents": "read"},
    }


@pytest.mark.parametrize("ids", [[], [0], ["42"], [True]])
def test_repository_token_refuses_a_malformed_scope(ids):
    with pytest.raises(github_service.GitHubAppError):
        asyncio.run(
            github_service.get_repository_id_scoped_token(
                "7", ids, {"contents": "read"}
            )
        )
