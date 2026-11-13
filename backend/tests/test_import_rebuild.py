"""Rebuild of imports on push and by the reconciler (CNP-244)."""

import asyncio
import hashlib
import hmac
import json
import uuid

import pytest
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

    def record(source_id, apps, trigger):
        world.rebuilds.append((source_id, apps, trigger))
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


def _apps(world) -> list:
    return [apps for _, apps, _ in world.rebuilds]


# ── Signature and replay ────────────────────────────────────────────────────


def test_a_signed_push_rebuilds_the_service_it_touched(client, world):
    _built(world)

    response = _send(client, _push(["tinyx/user-service/src/Main.java"]))

    assert response.status_code == 202
    assert _apps(world) == [{"user-service"}]


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

        again = rebuilds.remember_delivery(db, "d-1")

    assert again is True


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


# ── Paths ───────────────────────────────────────────────────────────────────


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
        (["docker-compose.yml"], "all"),
        (["deploy/compose.prod.yaml"], "all"),
        (["package.json"], "all"),
    ],
)
def test_only_the_services_a_push_touched_are_rebuilt(
    client, world, paths, expected
):
    _built(world)

    _send(client, _push(paths))

    if expected is None:
        assert world.rebuilds == []
    else:
        assert _apps(world) == [None if expected == "all" else expected]


@pytest.mark.parametrize(
    "payload",
    [
        _push(commits=[{"added": [], "modified": [], "removed": []}] * 2048),
        _push(commits=[{"added": [f"f{i}" for i in range(2048)]}]),
        _push(["README.md"], forced=True),
        _push(commits=[]),
        _push(commits=[{"modified": "frontend/x"}]),
    ],
    ids=["2048-commits", "2048-files", "forced", "no-commits", "malformed"],
)
def test_a_push_that_does_not_list_every_change_rebuilds_everything(
    client, world, payload
):
    _built(world)

    _send(client, payload)

    assert _apps(world) == [None]


def test_a_push_never_builds_the_sha_it_names(world, monkeypatch):
    source = _built(world)

    with world.db() as db:
        run = asyncio.run(
            service.rebuild(db, "alice", db.get(ImportSource, source.id))
        )

    assert run.sha == SHA != _push()["after"]


# ── Rebuild on behalf of the rebuild user ───────────────────────────────────


@pytest.fixture
def identity(world, monkeypatch):
    """The rebuild user's live standing, as Keycloak and GitHub tell it."""
    monkeypatch.setattr(rebuilds, "SessionLocal", world.Session)
    monkeypatch.setattr(
        rebuilds.audit, "record_system_event", lambda *a, **k: None
    )
    monkeypatch.setattr(
        rebuilds.notifications, "notify_project", lambda *a, **k: None
    )
    standing = {"role": ProjectRole.ADMIN, "enabled": True, "seen": []}

    def role(db, claims, project, minimum):
        standing["seen"].append(claims)
        if standing["role"] is None or standing["role"] != ProjectRole.ADMIN:
            raise HTTPException(403, "denied")

    monkeypatch.setattr(rebuilds, "assert_project_role", role)
    monkeypatch.setattr(
        rebuilds.keycloak_service,
        "get_user_by_id",
        lambda sub: {"id": sub, "enabled": standing["enabled"]},
    )
    return standing


def _auto(source, apps=None, trigger="push") -> str:
    return asyncio.run(rebuilds.auto_rebuild(source.id, apps, trigger))


def _reload(world, source) -> ImportSource:
    with world.db() as db:
        found = db.get(ImportSource, source.id)
        _ = found.runs, found.services
        return found


def test_the_rebuild_runs_as_the_creator_while_still_admin(world, identity):
    source = _built(world, rebuild_user="alice", rebuild_user_sub="uid-alice")

    outcome = _auto(source, {"frontend"})

    assert (outcome, world.dispatched[-1]["kind"]) == ("started", "build")
    assert identity["seen"][0] == {
        "preferred_username": "alice",
        "sub": "uid-alice",
    }


def test_the_rebuild_builds_only_the_services_asked(world, identity):
    source = _built(world)

    _auto(source, {"frontend"})

    with world.db() as db:
        build = db.get(ImportSource, source.id).runs[-1]
        assert service.built_apps(build) == {"frontend"}


def test_a_creator_no_longer_admin_leaves_the_rebuild_pending(world, identity):
    source = _built(world)
    identity["role"] = ProjectRole.MEMBER

    outcome = _auto(source, {"frontend"})

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
    _auto(source, {"frontend"})
    identity["role"] = ProjectRole.ADMIN

    outcome = _auto(source, {"user-service"})

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


@pytest.fixture
def reconciler(world, monkeypatch):
    monkeypatch.setattr(rebuilds, "SessionLocal", world.Session)
    state = {"head": BUILT_SHA, "changed": set(), "rebuilds": []}

    async def head(source):
        return state["head"]

    async def compare(source, base, current):
        return state["changed"]

    async def record(source_id, apps, trigger):
        state["rebuilds"].append((apps, trigger))
        return "started"

    monkeypatch.setattr(service, "_head", head)
    monkeypatch.setattr(rebuilds, "_compare", compare)
    monkeypatch.setattr(rebuilds, "auto_rebuild", record)
    return state


def test_the_reconciler_leaves_an_import_built_at_the_head(world, reconciler):
    source = _built(world)

    outcome = asyncio.run(rebuilds.reconcile_source(source.id))

    assert (outcome, reconciler["rebuilds"]) == ("up_to_date", [])


def test_the_reconciler_rebuilds_what_changed_since_the_last_build(
    world, reconciler
):
    source = _built(world)
    reconciler.update(head="c" * 40, changed={"frontend/index.html"})

    asyncio.run(rebuilds.reconcile_source(source.id))

    assert reconciler["rebuilds"] == [({"frontend"}, "reconcile")]


def test_the_reconciler_rebuilds_everything_when_github_cannot_compare(
    world, reconciler
):
    source = _built(world)
    reconciler.update(head="c" * 40, changed=None)

    asyncio.run(rebuilds.reconcile_source(source.id))

    assert reconciler["rebuilds"] == [(None, "reconcile")]


def test_the_reconciler_checks_a_head_with_nothing_to_build_once(
    world, reconciler
):
    source = _built(world)
    reconciler.update(head="c" * 40, changed={"README.md"})
    asyncio.run(rebuilds.reconcile_source(source.id))
    reconciler["changed"] = {"frontend/x"}

    second = asyncio.run(rebuilds.reconcile_source(source.id))

    assert (second, reconciler["rebuilds"]) == ("up_to_date", [])


def test_the_reconciler_skips_imports_with_auto_rebuild_off(world, reconciler):
    source = _built(world, auto_rebuild=False)
    reconciler.update(head="c" * 40, changed=None)

    asyncio.run(rebuilds.reconcile_source(source.id))

    assert reconciler["rebuilds"] == []


def test_the_reconciler_skips_a_rebuild_pending_approval(world, reconciler):
    source = _built(world, rebuild_pending={"reason": "not_admin"})
    reconciler.update(head="c" * 40, changed=None)

    asyncio.run(rebuilds.reconcile_source(source.id))

    assert reconciler["rebuilds"] == []


def test_access_check_uses_the_repository_id(world, identity, monkeypatch):
    source = _built(world)
    seen = []

    async def resolve(db, claims, name, repo_id=None):
        seen.append(repo_id)
        return REPO

    monkeypatch.setattr(access, "resolve_repository", resolve)

    _auto(source)

    assert seen == [REPO.repo_id]


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
