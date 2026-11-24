"""Copy mode of an import: the copy, its mirror run, and pulls from the original."""

import pytest

from app.core.config import settings
from app.models.imports import ImportRun, ImportSource
from app.services.github_service import GitHubAppError
from app.services.imports import access, copy_mode
from app.services.imports.access import RepoRef
from tests.test_import_api import (  # noqa: F401
    SHA,
    _call,
    _run,
    _source,
    client,
    world,
)

ORIGIN = RepoRef("acme/shop", 99, 11, "main", True)


@pytest.fixture
def github(world, monkeypatch):
    calls = {"created": [], "tokens": [], "pulls": [], "patched": []}
    taken: set[str] = set()

    async def create_repository(token, name, org_name, private, description):
        if name in taken:
            raise GitHubAppError(
                "Repository creation failed: name already exists"
            )
        calls["created"].append((org_name, name, private))
        return {
            "full_name": f"{org_name}/{name}",
            "id": 500 + len(calls["created"]),
        }

    async def scoped(installation, ids, permissions):
        calls["tokens"].append((installation, ids, permissions))
        return f"tok-{installation}-{ids[0]}"

    async def org_token():
        return "org"

    async def head(token, repo, branch):
        return SHA

    async def open_pull(token, repo, head_branch, base, title, body):
        calls["pulls"].append((repo, head_branch, base))
        return {"html_url": f"https://github.com/{repo}/pull/3"}

    async def patch_repo(token, method, path, what, **kwargs):
        calls["patched"].append((path, kwargs["json"]))

    async def resolve(db, claims, name):
        return ORIGIN

    monkeypatch.setattr(settings, "GITHUB_INSTALLATION_ID", "1")
    monkeypatch.setattr(copy_mode, "create_repository", create_repository)
    monkeypatch.setattr(copy_mode, "get_repository_id_scoped_token", scoped)
    monkeypatch.setattr(copy_mode, "get_org_installation_token", org_token)
    monkeypatch.setattr(copy_mode, "get_branch_head", head)
    monkeypatch.setattr(copy_mode, "open_pull_request", open_pull)
    monkeypatch.setattr(copy_mode, "_call", patch_repo)
    monkeypatch.setattr(access, "resolve_repository", resolve)
    monkeypatch.setattr(
        copy_mode.audit, "record_system_event", lambda *a, **k: None
    )
    calls["taken"] = taken
    return calls


def _copy_source(world, state="copied") -> ImportSource:
    return _source(
        world,
        source_mode="copy",
        origin_repo_full_name=ORIGIN.full_name,
        origin_repo_id=ORIGIN.repo_id,
        origin_installation_id=ORIGIN.installation_id,
        copy={"state": state},
    )


def test_import_is_in_place_by_default(client, world, github):
    client.post(
        "/api/projects/shop/imports", json={"repo_full_name": "acme/shop"}
    )

    with world.db() as db:
        source = db.query(ImportSource).one()
    assert (source.source_mode, source.repo_full_name, github["created"]) == (
        "in_place",
        "acme/shop",
        [],
    )


def test_copy_mode_creates_a_private_copy_and_points_the_import_at_it(
    client, world, github
):
    response = client.post(
        "/api/projects/shop/imports",
        json={"repo_full_name": "acme/shop", "source_mode": "copy"},
    )

    assert response.status_code == 202
    with world.db() as db:
        source = db.query(ImportSource).one()
    assert github["created"] == [("3-Istor", "shop-shop", True)]
    assert (
        source.repo_full_name,
        source.repo_id,
        source.installation_id,
        source.origin_repo_full_name,
        source.copy["state"],
    ) == ("3-Istor/shop-shop", 501, 1, "acme/shop", "pending")


def test_copy_name_moves_to_a_suffix_when_taken(client, world, github):
    github["taken"].add("shop-shop")

    client.post(
        "/api/projects/shop/imports",
        json={"repo_full_name": "acme/shop", "source_mode": "copy"},
    )

    assert github["created"] == [("3-Istor", "shop-shop-2", True)]


def test_copy_name_is_lowercase_and_safe():
    assert copy_mode.copy_name("Shop", "acme/My.Repo_x") == "shop-my.repo_x"


def test_reimporting_the_same_original_in_copy_mode_reuses_the_copy(
    client, world, github
):
    _copy_source(world)

    client.post(
        "/api/projects/shop/imports",
        json={"repo_full_name": "acme/shop", "source_mode": "copy"},
    )

    with world.db() as db:
        assert db.query(ImportSource).count() == 1
    assert github["created"] == []


def test_first_analysis_claim_asks_for_a_mirror_with_scoped_tokens(
    client, world, github
):
    run = _run(world, _copy_source(world, state="pending"))

    claim = _call(client, run, "claim").json()

    assert claim["copy"]["mode"] == "mirror"
    assert claim["copy"]["target"]["full_name"] == claim["repo_full_name"]
    assert ("11", [99], copy_mode.ORIGIN_READ) in github["tokens"]
    assert ("7", [42], copy_mode.COPY_PUSH) in github["tokens"]


def test_analysis_of_a_made_copy_claims_no_mirror(client, world, github):
    run = _run(world, _copy_source(world))

    claim = _call(client, run, "claim").json()

    assert "copy" not in claim


def test_in_place_claim_carries_no_copy(client, world, github):
    run = _run(world, _source(world))

    assert "copy" not in _call(client, run, "claim").json()


def test_copied_report_keeps_leak_locations_capped(client, world, github):
    run = _run(world, _copy_source(world, state="pending"), status="claimed")
    leaks = [
        {"rule": "aws", "file": "a.env", "commit": "c" * 40, "line": i}
        for i in range(250)
    ]

    response = _call(client, run, "copied", leaks_total=250, leaks=leaks[:200])

    assert response.status_code == 204
    with world.db() as db:
        copy = db.get(ImportSource, run.source_id).copy
    assert (copy["state"], copy["leaks_total"], len(copy["leaks"])) == (
        "copied",
        250,
        200,
    )
    assert github["patched"] == [
        ("/repos/3-Istor/epitweet", {"default_branch": "main"})
    ]


def test_copied_report_is_refused_for_an_in_place_import(
    client, world, github
):
    run = _run(world, _source(world), status="claimed")

    response = _call(client, run, "copied", leaks_total=0, leaks=[])

    assert response.status_code == 409


def test_pull_origin_starts_a_sync_run(client, world, github):
    source = _copy_source(world)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/pull-origin"
    )

    assert response.status_code == 202
    assert world.dispatched[-1]["kind"] == "sync"


def test_pull_origin_is_refused_for_an_in_place_import(client, world, github):
    source = _source(world)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/pull-origin"
    )

    assert response.status_code == 409


def test_pull_origin_waits_for_the_copy(client, world, github):
    source = _copy_source(world, state="pending")

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/pull-origin"
    )

    assert response.status_code == 409


def test_sync_claim_names_the_branch_to_push(client, world, github):
    run = _run(world, _copy_source(world), kind="sync")

    claim = _call(client, run, "claim").json()

    assert (
        claim["kind"],
        claim["copy"]["mode"],
        claim["copy"]["sync_branch"],
    ) == (
        "sync",
        "sync",
        f"cnp/sync-origin-{SHA[:8]}",
    )


def test_synced_branch_becomes_a_pull_request_on_the_copy(
    client, world, github
):
    run = _run(world, _copy_source(world), kind="sync", status="claimed")

    response = _call(
        client, run, "synced", branch=copy_mode.sync_branch(SHA), sha=SHA
    )

    assert (
        response.json()["pull"] == "https://github.com/3-Istor/epitweet/pull/3"
    )
    assert github["pulls"] == [
        ("3-Istor/epitweet", copy_mode.sync_branch(SHA), "main")
    ]
    with world.db() as db:
        assert db.get(ImportRun, run.id).status == "succeeded"


def test_synced_branch_other_than_the_claimed_one_is_refused(
    client, world, github
):
    run = _run(world, _copy_source(world), kind="sync", status="claimed")

    response = _call(
        client, run, "synced", branch="cnp/sync-origin-deadbeef", sha=SHA
    )

    assert response.status_code == 422
    assert github["pulls"] == []
