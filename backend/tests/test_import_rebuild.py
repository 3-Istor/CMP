"""Rebuild of imports on push and by the reconciler (CNP-244)."""

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import timedelta

import pytest
import requests
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.roles import ProjectRole
from app.models.imports import ImportRun, ImportService, ImportSource
from app.routers import imports as imports_router
from app.services import audit, authz
from app.services.imports import access, rebuilds, service
from tests.test_import_api import (  # noqa: F401
    DIGEST,
    PLAN,
    REPO,
    SHA,
    _role_override,
    _run,
    _source,
    world,
)

SECRET = "webhook-secret"
BUILT_SHA = "b" * 40
SERVICES = {
    "user-service": "tinyx/user-service",
    "post-service": "tinyx/post-service",
    "frontend": "frontend",
}
AUTO_REBUILD = rebuilds.auto_rebuild


@pytest.fixture
def client(world, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(rebuilds, "SessionLocal", world.Session)
    monkeypatch.setattr(rebuilds, "_last_refusal", {"at": 0.0})
    world.journal = []
    world.rebuilds = []
    world.notified = []

    def journal(action, **kwargs):
        world.journal.append((action, kwargs))

    def record(source_id, trigger):
        world.rebuilds.append((source_id, trigger))
        return asyncio.sleep(0)

    monkeypatch.setattr(rebuilds.audit, "record_system_event", journal)
    monkeypatch.setattr(rebuilds, "auto_rebuild", record)
    monkeypatch.setattr(
        rebuilds.notifications,
        "notify_project",
        lambda project, **kwargs: world.notified.append(project),
    )
    app = FastAPI()
    app.include_router(imports_router.router, prefix="/api")
    app.include_router(imports_router.webhooks, prefix="/api")

    def get_session():
        db = world.db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_session
    for annotation, minimum in (
        (authz.RequireAdmin, ProjectRole.ADMIN),
        (authz.RequireMember, ProjectRole.MEMBER),
    ):
        app.dependency_overrides[annotation.__metadata__[0].dependency] = (
            _role_override(world, minimum)
        )
    return TestClient(app)


def _built(world, **fields) -> ImportSource:
    """An import of three services, built at BUILT_SHA."""
    source = _source(world, **fields)
    _run(world, source, status="succeeded", plan=PLAN)
    with world.db() as db:
        for app, path in SERVICES.items():
            db.add(
                ImportService(
                    source_id=source.id,
                    app_name=app,
                    kind="service",
                    path=path,
                    spec={"plan_name": app},
                    last_digest=DIGEST,
                    last_sha=BUILT_SHA,
                    built_sha=BUILT_SHA,
                )
            )
        db.commit()
    build = _run(
        world,
        source,
        kind="build",
        status="succeeded",
        plan={"plan": PLAN, "edits": {}},
    )
    with world.db() as db:
        db.get(ImportRun, build.id).sha = BUILT_SHA
        db.commit()
    return source


def _push(paths=(), **overrides) -> dict:
    return {
        "ref": "refs/heads/main",
        "after": "f" * 40,
        "installation": {"id": REPO.installation_id},
        "repository": {"id": REPO.repo_id, "full_name": REPO.full_name},
        "commits": [{"added": [], "modified": list(paths), "removed": []}],
        **overrides,
    }


def _send(client, payload, *, secret=SECRET, delivery=None, event="push"):
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    if secret is not None:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={digest}"
    return client.post(
        "/api/import/webhooks/github", content=body, headers=headers
    )


# ── Signature and replay ────────────────────────────────────────────────────


def test_a_signed_push_starts_a_rebuild_of_its_import(client, world):
    source = _built(world)

    response = _send(client, _push(["tinyx/user-service/src/Main.java"]))

    assert response.status_code == 202
    assert world.rebuilds == [(source.id, "push")]


def test_a_delivery_signed_with_another_secret_is_refused(client, world):
    _built(world)

    response = _send(client, _push(["frontend/x"]), secret="guess")

    assert (response.status_code, world.rebuilds) == (401, [])


def test_a_delivery_without_signature_is_refused(client, world):
    _built(world)

    response = _send(client, _push(["frontend/x"]), secret=None)

    assert (response.status_code, world.rebuilds) == (401, [])


def test_a_refused_delivery_is_journaled(client, world):
    _send(client, _push(), secret="guess")

    assert [a for a, _ in world.journal] == ["import.webhook.refused"]


def test_refused_deliveries_cannot_flood_the_journal(client, world):
    for _ in range(5):
        _send(client, _push(), secret="guess")

    assert len(world.journal) == 1


def test_every_delivery_is_refused_while_no_secret_is_configured(
    client, world, monkeypatch
):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "")
    _built(world)

    response = _send(client, _push(["frontend/x"]), secret="")

    assert (response.status_code, world.rebuilds) == (503, [])


def test_a_body_above_the_cap_is_refused_before_it_is_verified(
    client, world, monkeypatch
):
    monkeypatch.setattr(settings, "IMPORT_WEBHOOK_MAX_BODY_BYTES", 64)

    response = _send(client, _push(["x" * 200]))

    assert response.status_code == 413


def test_a_replayed_delivery_rebuilds_once(client, world):
    _built(world)
    delivery = str(uuid.uuid4())

    first = _send(client, _push(["frontend/x"]), delivery=delivery)
    second = _send(client, _push(["frontend/x"]), delivery=delivery)

    assert (first.json()["status"], second.json()["status"]) == (
        "accepted",
        "duplicate",
    )
    assert len(world.rebuilds) == 1


def test_a_delivery_seen_more_than_a_day_ago_is_forgotten(world):
    with world.db() as db:
        assert rebuilds.remember_delivery(db, "d-1")
        row = db.get(rebuilds.ImportWebhookDelivery, "d-1")
        row.received_at = rebuilds._now() - rebuilds.DELIVERY_TTL * 2
        db.commit()

        seen = rebuilds.seen_delivery(db, "d-1")
        again = rebuilds.remember_delivery(db, "d-1")

    assert (seen, again) == (False, True)


def test_a_delivery_that_failed_is_processed_when_github_redelivers_it(
    client, world, monkeypatch
):
    source = _built(world)
    delivery = str(uuid.uuid4())
    real = rebuilds.on_push
    calls = []

    def flaky(db, payload, delivery_id):
        calls.append(delivery_id)
        if len(calls) == 1:
            raise RuntimeError("database locked")
        return real(db, payload, delivery_id)

    monkeypatch.setattr(rebuilds, "on_push", flaky)
    with pytest.raises(RuntimeError):
        _send(client, _push(["frontend/x"]), delivery=delivery)

    again = _send(client, _push(["frontend/x"]), delivery=delivery)

    assert again.json()["status"] == "accepted"
    assert world.rebuilds == [(source.id, "push")]


def test_a_replayed_push_with_a_new_delivery_id_is_a_no_op(
    client, world, identity, monkeypatch
):
    source = _built(world)
    spawned = []
    monkeypatch.setattr(rebuilds, "auto_rebuild", AUTO_REBUILD)
    monkeypatch.setattr(service, "_spawn", spawned.append)
    body = _push(["frontend/x"])

    def deliver() -> list:
        spawned.clear()
        _send(client, body)
        return [asyncio.run(coro) for coro in spawned]

    first = deliver()
    while_building = deliver()
    _finish(world, source)
    after_build = deliver()

    assert (first, while_building, after_build) == (
        ["started"],
        ["busy"],
        ["up_to_date"],
    )
    assert len(world.dispatched) == 1


def test_events_other_than_push_rebuild_nothing(client, world):
    _built(world)

    response = _send(client, _push(["frontend/x"]), event="issues")

    assert (response.json()["status"], world.rebuilds) == ("ignored", [])


# ── Matching ────────────────────────────────────────────────────────────────


def test_a_push_from_an_unknown_installation_rebuilds_nothing(client, world):
    _built(world)

    _send(client, _push(["frontend/x"], installation={"id": 999}))

    assert world.rebuilds == []


def test_a_repository_matching_by_name_only_rebuilds_nothing(client, world):
    _built(world)

    _send(
        client,
        _push(
            ["frontend/x"],
            repository={"id": 4242, "full_name": REPO.full_name},
        ),
    )

    assert world.rebuilds == []


def test_a_push_to_another_branch_rebuilds_nothing(client, world):
    _built(world)

    _send(client, _push(["frontend/x"], ref="refs/heads/feature"))

    assert world.rebuilds == []


def test_a_push_to_an_import_with_auto_rebuild_off_rebuilds_nothing(
    client, world
):
    _built(world, auto_rebuild=False)

    _send(client, _push(["frontend/x"]))

    assert world.rebuilds == []


def test_a_push_to_an_import_never_applied_rebuilds_nothing(client, world):
    source = _source(world)
    _run(world, source, status="succeeded", plan=PLAN)

    _send(client, _push(["frontend/x"]))

    assert world.rebuilds == []


# ── Rebuild on behalf of the rebuild user ───────────────────────────────────


@pytest.fixture
def identity(world, monkeypatch):
    """
    The rebuild user's live standing, as Keycloak tells it, and the
    repository, as GitHub tells it: its branch head and what changed
    between two commits.
    """
    monkeypatch.setattr(rebuilds, "SessionLocal", world.Session)
    monkeypatch.setattr(
        rebuilds.audit, "record_system_event", lambda *a, **k: None
    )
    monkeypatch.setattr(
        rebuilds.notifications, "notify_project", lambda *a, **k: None
    )
    standing = {
        "role": ProjectRole.ADMIN,
        "enabled": True,
        "users": {"alice": "uid-alice"},
        "seen": [],
        "head": SHA,
        "changed": {"frontend/x"},
        "compared": [],
    }

    def role(db, claims, project, minimum):
        standing["seen"].append(claims)
        if standing["role"] is None or standing["role"] != ProjectRole.ADMIN:
            raise HTTPException(403, "denied")

    def by_username(username):
        if standing["users"] is None:
            raise requests.ConnectionError("Keycloak down")
        sub = standing["users"].get(username)
        return {"id": sub, "username": username} if sub else None

    async def head(source):
        return standing["head"]

    async def compare(source, base, current):
        standing["compared"].append((base, current))
        changed = standing["changed"]
        return changed(base, current) if callable(changed) else changed

    monkeypatch.setattr(rebuilds, "assert_project_role", role)
    monkeypatch.setattr(
        rebuilds.keycloak_service,
        "get_user_by_id",
        lambda sub: {"id": sub, "enabled": standing["enabled"]},
    )
    monkeypatch.setattr(
        rebuilds.keycloak_service, "get_user_by_username", by_username
    )
    monkeypatch.setattr(service, "_head", head)
    monkeypatch.setattr(rebuilds, "_compare", compare)
    return standing


def _auto(source, trigger="push") -> str:
    return asyncio.run(rebuilds.auto_rebuild(source.id, trigger))


def _reload(world, source) -> ImportSource:
    with world.db() as db:
        found = db.get(ImportSource, source.id)
        _ = found.runs, found.services
        return found


def _last_built(world, source) -> set[str]:
    with world.db() as db:
        return service.built_apps(db.get(ImportSource, source.id).runs[-1])


def _finish(world, source) -> None:
    """The last build publishes its images and deploys."""
    with world.db() as db:
        run = db.get(ImportSource, source.id).runs[-1]
        for row in run.source.services:
            if row.app_name in service.built_apps(run):
                row.built_sha = run.sha
                row.last_digest = DIGEST
        run.status = "succeeded"
        db.commit()


def test_the_rebuild_runs_as_the_creator_while_still_admin(world, identity):
    source = _built(world, rebuild_user="alice", rebuild_user_sub="uid-alice")

    outcome = _auto(source)

    assert (outcome, world.dispatched[-1]["kind"]) == ("started", "build")
    assert identity["seen"][0] == {
        "preferred_username": "alice",
        "sub": "uid-alice",
    }


def test_a_rebuild_user_without_sub_is_found_in_keycloak_by_username(
    world, identity
):
    source = _built(world)

    outcome = _auto(source)

    assert (outcome, identity["seen"][0]["sub"]) == ("started", "uid-alice")
    assert _reload(world, source).rebuild_user_sub == "uid-alice"


def test_a_rebuild_user_found_by_username_is_journaled(
    world, identity, monkeypatch
):
    journal = []
    monkeypatch.setattr(
        rebuilds.audit,
        "record_system_event",
        lambda action, **kwargs: journal.append((action, kwargs)),
    )
    source = _built(world)

    _auto(source)

    resolved = [
        k for action, k in journal if action == "import.rebuild.user_resolved"
    ]
    assert [r["details"] for r in resolved] == [
        {"user": "alice", "sub": "uid-alice", "by": "username"}
    ]


def test_a_rebuild_user_with_a_sub_is_not_journaled_as_resolved(
    world, identity, monkeypatch
):
    journal = []
    monkeypatch.setattr(
        rebuilds.audit,
        "record_system_event",
        lambda action, **kwargs: journal.append(action),
    )
    source = _built(world, rebuild_user_sub="uid-alice")

    _auto(source)

    assert "import.rebuild.user_resolved" not in journal


def test_a_reconcile_pass_fails_runs_stuck_past_the_timeout(world, identity):
    source = _built(world, auto_rebuild=False)
    stuck = _run(
        world,
        source,
        status="claimed",
        updated_at=service._now()
        - timedelta(minutes=settings.IMPORT_BUILD_TIMEOUT_MINUTES + 1),
    )

    asyncio.run(rebuilds.reconcile_once())

    with world.db() as db:
        assert db.get(ImportRun, stuck.id).status == "failed"


@pytest.mark.parametrize("users", [{}, None], ids=["unknown", "outage"])
def test_a_rebuild_user_keycloak_cannot_find_leaves_the_rebuild_pending(
    world, identity, users
):
    source = _built(world)
    identity["users"] = users

    outcome = _auto(source)

    assert (outcome, world.dispatched) == ("pending", [])
    assert _reload(world, source).rebuild_pending["reason"] == "unknown_user"


def test_a_creator_no_longer_admin_leaves_the_rebuild_pending(world, identity):
    source = _built(world)
    identity["role"] = ProjectRole.MEMBER

    outcome = _auto(source)

    assert (outcome, world.dispatched) == ("pending", [])
    assert _reload(world, source).rebuild_pending["reason"] == "not_admin"


def test_a_disabled_creator_leaves_the_rebuild_pending(world, identity):
    source = _built(world, rebuild_user_sub="uid-alice")
    identity["enabled"] = False

    _auto(source)

    assert _reload(world, source).rebuild_pending["reason"] == "inactive"


def test_a_creator_who_lost_the_repository_leaves_the_rebuild_pending(
    world, identity
):
    source = _built(world)
    world.repos = []

    _auto(source)

    assert _reload(world, source).rebuild_pending["reason"] == "repo_access"


def test_a_pending_rebuild_stays_pending_on_the_next_push(world, identity):
    source = _built(world)
    identity["role"] = ProjectRole.MEMBER
    _auto(source)
    identity["role"] = ProjectRole.ADMIN
    identity["changed"] = {"tinyx/user-service/x"}

    outcome = _auto(source)

    assert (outcome, world.dispatched) == ("pending", [])
    assert _reload(world, source).rebuild_pending["apps"] == [
        "frontend",
        "user-service",
    ]


def test_a_keycloak_outage_defers_the_rebuild_without_marking_it(
    world, identity, monkeypatch
):
    source = _built(world)

    def down(*args):
        raise HTTPException(503, "Keycloak down")

    monkeypatch.setattr(rebuilds, "assert_project_role", down)

    outcome = _auto(source)

    assert (outcome, _reload(world, source).rebuild_pending) == (
        "deferred",
        None,
    )


def test_an_admin_confirms_a_pending_rebuild_and_becomes_its_user(
    client, world
):
    source = _built(
        world,
        rebuild_pending={"reason": "not_admin", "user": "bob", "apps": None},
    )

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert response.status_code == 202
    found = _reload(world, source)
    assert (found.rebuild_pending, found.rebuild_user) == (None, "alice")


def test_confirming_a_pending_rebuild_needs_step_up(
    client, world, monkeypatch
):
    monkeypatch.setattr(settings, "STEP_UP_ENABLED", True)
    source = _built(world, rebuild_pending={"reason": "not_admin"})

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert response.status_code == 401


def test_access_check_uses_the_repository_id(world, identity, monkeypatch):
    source = _built(world)
    seen = []

    async def resolve(db, claims, name, repo_id=None):
        seen.append(repo_id)
        return REPO

    monkeypatch.setattr(access, "resolve_repository", resolve)

    _auto(source)

    assert seen == [REPO.repo_id]


# ── What a rebuild builds ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "paths,expected",
    [
        (["README.md", "docs/setup.md"], None),
        (
            ["frontend/src/app.ts", "tinyx/post-service/pom.xml"],
            {"frontend", "post-service"},
        ),
        (["tinyx/user-service/Dockerfile"], {"user-service"}),
        (["tinyx/pom.xml"], {"user-service", "post-service"}),
        (["tinyx/common/src/Util.java"], {"user-service", "post-service"}),
        (["docker-compose.yml"], set(SERVICES)),
        (["deploy/compose.prod.yaml"], set(SERVICES)),
        (["package.json"], set(SERVICES)),
    ],
)
def test_only_the_services_whose_files_changed_since_their_build_are_rebuilt(
    world, identity, paths, expected
):
    source = _built(world)
    identity["changed"] = set(paths)

    outcome = _auto(source)

    if expected is None:
        assert (outcome, world.dispatched) == ("up_to_date", [])
    else:
        assert _last_built(world, source) == expected


def test_changes_are_compared_from_the_commit_the_services_were_built_at(
    world, identity
):
    source = _built(world)

    _auto(source)

    assert identity["compared"] == [(BUILT_SHA, SHA)]


def test_a_comparison_github_cannot_give_rebuilds_everything(world, identity):
    source = _built(world)
    identity["changed"] = None

    _auto(source)

    assert _last_built(world, source) == set(SERVICES)


def test_a_change_pushed_while_a_build_runs_is_built_after_it(world, identity):
    source = _built(world)
    second, third = "2" * 40, "3" * 40
    history = [
        (BUILT_SHA, set()),
        (second, {"tinyx/user-service/src/A.java"}),
        (third, {"tinyx/post-service/src/B.java"}),
    ]
    commits = [sha for sha, _ in history]

    def changed(base, head):
        span = history[commits.index(base) + 1 : commits.index(head) + 1]
        return set().union(*(paths for _, paths in span))

    identity["changed"] = changed
    identity["head"] = second
    first = _auto(source)
    identity["head"] = third
    while_busy = _auto(source)
    _finish(world, source)

    after = _auto(source, trigger="reconcile")

    assert (first, while_busy, after) == ("started", "busy", "started")
    assert _last_built(world, source) == {"post-service"}


def test_a_push_never_builds_the_sha_it_names(world, monkeypatch):
    source = _built(world)

    with world.db() as db:
        run = asyncio.run(
            service.rebuild(db, "alice", db.get(ImportSource, source.id))
        )

    assert run.sha == SHA != _push()["after"]


# ── Setting ─────────────────────────────────────────────────────────────────


def test_an_admin_turns_auto_rebuild_off_without_step_up(
    client, world, monkeypatch
):
    monkeypatch.setattr(settings, "STEP_UP_ENABLED", True)
    source = _built(world)

    response = client.put(
        f"/api/projects/shop/imports/{source.id}/settings",
        json={"auto_rebuild": False},
    )

    assert (response.status_code, response.json()["auto_rebuild"]) == (
        200,
        False,
    )


def test_turning_auto_rebuild_on_needs_step_up(client, world, monkeypatch):
    monkeypatch.setattr(settings, "STEP_UP_ENABLED", True)
    source = _built(world, auto_rebuild=False)

    response = client.put(
        f"/api/projects/shop/imports/{source.id}/settings",
        json={"auto_rebuild": True},
    )

    assert response.status_code == 401
    assert _reload(world, source).auto_rebuild is False


def test_a_member_cannot_change_auto_rebuild(client, world):
    world.roles["shop"] = ProjectRole.MEMBER
    source = _built(world)

    response = client.put(
        f"/api/projects/shop/imports/{source.id}/settings",
        json={"auto_rebuild": False},
    )

    assert response.status_code == 403


def test_imports_show_auto_rebuild_on_by_default(client, world):
    _built(world)

    listed = client.get("/api/projects/shop/imports").json()["imports"]

    assert listed[0]["auto_rebuild"] is True


# ── Partial builds ──────────────────────────────────────────────────────────


def _partial(world, apps) -> ImportRun:
    source = _built(world)
    return _run(
        world,
        source,
        kind="build",
        status="claimed",
        plan={"plan": PLAN, "edits": {}, "apps": apps},
    )


def test_a_partial_build_hands_the_worker_only_its_recipes(world):
    run = _partial(world, ["frontend"])

    with world.db() as db:
        recipes = service._recipes(db.get(ImportRun, run.id))

    assert [r["app"] for r in recipes] == ["frontend"]


def test_a_partial_build_publishes_only_its_services(world):
    run = _partial(world, ["frontend"])
    image = service.image_name("shop", "frontend")

    with world.db() as db:
        service.receive_publish(
            db,
            db.get(ImportRun, run.id),
            [{"app": "frontend", "image": image, "digest": DIGEST}],
        )
        status = db.get(ImportRun, run.id).status

    assert status == "running"


# ── Reconciler ──────────────────────────────────────────────────────────────


def _reconcile(source) -> str:
    return asyncio.run(rebuilds.reconcile_source(source.id))


def test_the_reconciler_leaves_an_import_built_at_the_head(world, identity):
    source = _built(world)
    identity["head"] = BUILT_SHA

    outcome = _reconcile(source)

    assert (outcome, world.dispatched) == ("up_to_date", [])


def test_the_reconciler_rebuilds_what_changed_since_the_last_build(
    world, identity
):
    source = _built(world)
    identity["changed"] = {"frontend/index.html"}

    outcome = _reconcile(source)

    assert (outcome, _last_built(world, source)) == ("started", {"frontend"})


def test_the_reconciler_checks_a_head_with_nothing_to_build_once(
    world, identity
):
    source = _built(world)
    identity["changed"] = {"README.md"}
    _reconcile(source)
    identity["changed"] = {"frontend/x"}

    second = _reconcile(source)

    assert (second, len(identity["compared"]), world.dispatched) == (
        "up_to_date",
        1,
        [],
    )


def test_the_reconciler_skips_imports_with_auto_rebuild_off(world, identity):
    source = _built(world, auto_rebuild=False)

    outcome = _reconcile(source)

    assert (outcome, world.dispatched) == ("skipped", [])


def test_the_reconciler_skips_a_rebuild_pending_approval(world, identity):
    source = _built(world, rebuild_pending={"reason": "not_admin"})

    outcome = _reconcile(source)

    assert (outcome, world.dispatched) == ("skipped", [])


def test_a_reconcile_pass_never_runs_twice_at_once(world, identity):
    async def overlapping():
        async with rebuilds._reconcile_lock:
            await rebuilds.reconcile_once()

    _built(world)

    asyncio.run(overlapping())

    assert world.dispatched == []


def test_a_streamed_body_above_the_cap_is_refused_behind_the_journal(
    world, monkeypatch
):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "IMPORT_WEBHOOK_MAX_BODY_BYTES", 1024)
    app = FastAPI()
    app.include_router(imports_router.webhooks, prefix="/api")
    app.add_middleware(audit.AuditMiddleware)
    app.dependency_overrides[get_db] = world.db

    def chunks():
        for _ in range(64):
            yield b"x" * 1024

    response = TestClient(app).post(
        "/api/import/webhooks/github", content=chunks()
    )

    assert response.status_code == 413
