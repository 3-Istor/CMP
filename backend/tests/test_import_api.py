import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.roles import ProjectRole, at_least
from app.models.deployment import Deployment
from app.models.imports import ImportRun, ImportService, ImportSource
from app.routers import imports as imports_router
from app.services import authz
from app.services.imports import access, generate, service
from app.services.imports.access import RepoRef

PLAN = json.loads(
    (
        Path(__file__).parent / "fixtures" / "imports" / "epitweet-plan.json"
    ).read_text()
)
SHA = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "c" * 64
REPO = RepoRef("3-Istor/epitweet", 42, 7, "main", True)


class World:
    def __init__(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, expire_on_commit=False)
        self.roles: dict[str, ProjectRole] = {"shop": ProjectRole.ADMIN}
        self.claims = {"run_id": "555", "run_attempt": "1"}
        self.repos: list[RepoRef] = [REPO]
        self.dispatched: list[dict] = []
        self.deployed: list[str] = []

    def db(self):
        return self.Session()


def _role_override(world: World, minimum: ProjectRole):
    def dep(project_name: str):
        role = world.roles.get(project_name)
        if role is None or not at_least(role, minimum):
            raise HTTPException(403, "denied")
        return authz.ProjectContext(
            project_name=project_name,
            user_id="uid-alice",
            username="alice",
            role=role,
            token={"preferred_username": "alice"},
        )

    return dep


@pytest.fixture
def world(monkeypatch):
    world = World()
    monkeypatch.setattr(settings, "IMPORT_ENABLED", True)
    monkeypatch.setattr(settings, "STEP_UP_ENABLED", False)

    async def head(source):
        return SHA

    async def dispatch(db, run):
        world.dispatched.append({"run_id": run.id, "kind": run.kind})

    async def repo_token(source):
        return "ghs_scopedtoken"

    async def records():
        return []

    async def deploy(run_id):
        world.deployed.append(run_id)

    async def importable(db, claims):
        return list(world.repos)

    monkeypatch.setattr(access, "importable_repositories", importable)
    monkeypatch.setattr(service, "SessionLocal", world.Session)
    monkeypatch.setattr(service, "_head", head)
    monkeypatch.setattr(service, "_dispatch", dispatch)
    monkeypatch.setattr(service, "_repo_token", repo_token)
    monkeypatch.setattr(service, "list_records", records)
    monkeypatch.setattr("app.services.imports.deploy.deploy", deploy)
    monkeypatch.setattr(service, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr(
        service.audit, "record_system_event", lambda *a, **k: None
    )
    return world


@pytest.fixture
def client(world):
    app = FastAPI()
    app.include_router(imports_router.router, prefix="/api")
    app.include_router(imports_router.worker, prefix="/api")

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
    app.dependency_overrides[imports_router.worker_claims] = (
        lambda: world.claims
    )
    return TestClient(app)


def _source(world, project="shop", **fields) -> ImportSource:
    with world.db() as db:
        source = ImportSource(
            project_name=project,
            repo_full_name="3-Istor/epitweet",
            repo_id=42,
            installation_id=7,
            branch="main",
            created_by="alice",
            **fields,
        )
        db.add(source)
        db.commit()
        return source


def _run(world, source, kind="analyze", status="queued", **fields):
    with world.db() as db:
        run = ImportRun(
            source_id=source.id,
            kind=kind,
            sha=SHA,
            status=status,
            requested_by="alice",
            expires_at=fields.pop(
                "expires_at", service._now() + timedelta(hours=1)
            ),
            **fields,
        )
        db.add(run)
        db.commit()
        return run


def _call(client, run, action, **body):
    return client.post(
        f"/api/import/worker/{run.id}/{action}",
        json={"attempt_id": run.attempt_id, **body},
    )


# ── User API ────────────────────────────────────────────────────────────────


def test_import_is_hidden_while_the_feature_is_off(client, monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_ENABLED", False)

    response = client.get("/api/projects/shop/imports")

    assert response.status_code == 404


def test_admin_starts_an_analysis_of_a_repository(client, world, monkeypatch):
    async def resolve(db, claims, name):
        return REPO

    monkeypatch.setattr(access, "resolve_repository", resolve)

    response = client.post(
        "/api/projects/shop/imports", json={"repo_full_name": REPO.full_name}
    )

    assert response.status_code == 202
    assert response.json()["run"]["status"] == "queued"
    assert world.dispatched == [
        {"run_id": response.json()["run"]["id"], "kind": "analyze"}
    ]


def test_member_cannot_start_an_import(client, world):
    world.roles["shop"] = ProjectRole.MEMBER

    response = client.post(
        "/api/projects/shop/imports", json={"repo_full_name": REPO.full_name}
    )

    assert response.status_code == 403


def test_platform_repository_cannot_be_imported(client, world):
    response = client.post(
        "/api/projects/shop/imports", json={"repo_full_name": "3-Istor/K3s"}
    )

    assert response.status_code == 403


def test_run_of_another_project_is_not_found(client, world):
    world.roles["other"] = ProjectRole.ADMIN
    run = _run(world, _source(world, project="other"))

    response = client.get(f"/api/projects/shop/imports/runs/{run.id}")

    assert response.status_code == 404


def test_apply_on_an_import_of_another_project_is_not_found(client, world):
    source = _source(world, project="other")

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id=r-1", json={}
    )

    assert response.status_code == 404


def test_member_reads_imports_but_cannot_apply(client, world):
    world.roles["shop"] = ProjectRole.MEMBER
    source = _source(world)
    analysis = _run(world, source, status="succeeded", plan=PLAN)

    listed = client.get("/api/projects/shop/imports")
    applied = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={},
    )

    assert listed.status_code == 200
    assert applied.status_code == 403


def test_apply_needs_step_up(client, world, monkeypatch):
    monkeypatch.setattr(settings, "STEP_UP_ENABLED", True)
    source = _source(world)
    analysis = _run(world, source, status="succeeded", plan=PLAN)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={},
    )

    assert response.status_code == 401


def test_apply_stores_the_apps_and_starts_a_build(client, world):
    source = _source(world)
    analysis = _run(world, source, status="succeeded", plan=PLAN)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={"exclude": ["search-service", "elasticsearch"]},
    )

    assert response.status_code == 202
    assert response.json()["run"]["kind"] == "build"
    with world.db() as db:
        names = {s.app_name for s in db.query(ImportService).all()}
    assert len(names) == 9 and "search-service" not in names


def test_apply_takes_port_probe_and_link_edits(client, world):
    source = _source(world)
    analysis = _run(world, source, status="succeeded", plan=PLAN)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={
            "port": {"user-service": 9090},
            "probe": {
                "user-service": {
                    "kind": "http",
                    "path": "/health",
                    "port": 9090,
                }
            },
            "drop_links": [{"from": "user-service", "to": "redis"}],
        },
    )

    assert response.status_code == 202
    with world.db() as db:
        spec = (
            db.query(ImportService).filter_by(app_name="user-service").one()
        ).spec
    assert (spec["probe"], [d["app"] for d in spec["depends_on"]]) == (
        {"kind": "http", "path": "/health", "port": 9090},
        ["mongodb"],
    )


def test_links_drawn_on_the_map_survive_a_new_apply(client, world):
    source = _source(world)
    analysis = _run(world, source, status="succeeded", plan=PLAN)
    client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={},
    )
    with world.db() as db:
        row = db.query(ImportService).filter_by(app_name="frontend").one()
        drawn = {
            "app": "neo4j",
            "port": 7687,
            "alias": "graph",
            "manual": True,
        }
        row.spec = {**row.spec, "depends_on": [*row.spec["depends_on"], drawn]}
        db.query(ImportRun).filter_by(kind="build").update(
            {"status": "succeeded"}
        )
        db.commit()

    client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={},
    )

    with world.db() as db:
        row = db.query(ImportService).filter_by(app_name="frontend").one()
        assert drawn in row.spec["depends_on"]


def test_run_view_carries_the_capacity_the_plan_is_held_to(client, world):
    source = _source(world)
    run = _run(world, source, status="succeeded", plan=PLAN)

    body = client.get(f"/api/projects/shop/imports/runs/{run.id}").json()

    assert body["capacity"]["project_max_mib"] == (
        settings.IMPORT_MAX_PROJECT_REQUESTS_MIB
    )
    assert body["capacity"]["backing_requests_mib"]["mongodb"] > 0


def test_apply_refuses_a_name_held_by_an_app_of_the_project(client, world):
    source = _source(world)
    analysis = _run(world, source, status="succeeded", plan=PLAN)
    with world.db() as db:
        db.add(
            Deployment(
                name="frontend", template_id="static", project_id="shop"
            )
        )
        db.commit()

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={},
    )

    assert response.status_code == 422
    assert "'frontend' is already an app of the project" in response.text


def test_apply_refuses_while_a_run_is_in_progress(client, world):
    source = _source(world)
    analysis = _run(world, source, status="succeeded", plan=PLAN)
    _run(world, source, kind="build", status="claimed")

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={},
    )

    assert response.status_code == 409


def test_apply_of_an_analysis_that_is_no_longer_the_latest_is_refused(
    client, world
):
    source = _source(world)
    reviewed = _run(world, source, status="succeeded", plan=PLAN)
    _run(world, source, status="succeeded", plan=PLAN)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={reviewed.id}",
        json={},
    )

    assert response.status_code == 409
    assert "review again" in response.text
    assert world.dispatched == []


def test_a_re_analysis_between_review_and_apply_is_never_applied(
    client, world
):
    source = _source(world)
    reviewed = _run(world, source, status="succeeded", plan=PLAN)
    newer = _run(world, source, status="queued")
    with world.db() as db:
        db.get(ImportRun, newer.id).status = "succeeded"
        db.get(ImportRun, newer.id).plan = PLAN
        db.commit()

    stale = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={reviewed.id}",
        json={},
    )

    assert stale.status_code == 409
    with world.db() as db:
        assert db.query(ImportService).count() == 0


def test_a_second_active_run_of_an_import_is_refused_by_the_database(world):
    source = _source(world)
    _run(world, source, kind="build", status="claimed")

    with world.db() as db, pytest.raises(service.ImportRefused):
        service._new_run(
            db,
            db.get(ImportSource, source.id),
            service.RunKind.ANALYZE,
            SHA,
            "alice",
        )


def test_secret_env_values_are_hidden_from_users(client, world):
    plan = json.loads(json.dumps(PLAN))
    plan["services"][0]["env"].append(
        {
            "name": "DB_PASSWORD",
            "value": "hunter2",
            "from_link": None,
            "secret": False,
        }
    )
    run = _run(world, _source(world), status="succeeded", plan=plan)

    response = client.get(f"/api/projects/shop/imports/runs/{run.id}")

    assert "hunter2" not in response.text


# ── Worker API ──────────────────────────────────────────────────────────────


def test_claim_returns_the_scoped_token_and_binds_the_workflow_run(
    client, world
):
    run = _run(world, _source(world))

    response = _call(client, run, "claim")

    assert response.status_code == 200
    assert response.json()["token"] == "ghs_scopedtoken"
    assert response.json()["sha"] == SHA
    with world.db() as db:
        stored = db.get(ImportRun, run.id)
        assert (stored.status, stored.workflow_run_id) == ("claimed", "555")


def test_claim_is_single_use(client, world):
    run = _run(world, _source(world))
    _call(client, run, "claim")

    replay = _call(client, run, "claim")

    assert replay.status_code == 409


def test_another_attempt_is_refused(client, world):
    run = _run(world, _source(world))

    response = client.post(
        f"/api/import/worker/{run.id}/claim",
        json={"attempt_id": "00000000-0000-0000-0000-000000000000"},
    )

    assert response.status_code == 403


def test_another_workflow_run_cannot_act_on_a_claimed_run(client, world):
    run = _run(world, _source(world))
    _call(client, run, "claim")
    world.claims = {"run_id": "999"}

    response = _call(client, run, "plan", plan=PLAN)

    assert response.status_code == 403


def test_expired_run_is_refused_and_failed(client, world):
    run = _run(
        world,
        _source(world),
        expires_at=service._now() - timedelta(minutes=1),
    )

    response = _call(client, run, "claim")

    assert response.status_code == 410
    with world.db() as db:
        assert db.get(ImportRun, run.id).status == "failed"


def test_plan_is_validated_against_the_analyzer_schema(client, world):
    run = _run(world, _source(world))
    _call(client, run, "claim")

    response = _call(client, run, "plan", plan={**PLAN, "extra": 1})

    assert response.status_code == 422


def test_plan_for_another_commit_is_refused(client, world):
    run = _run(world, _source(world))
    _call(client, run, "claim")
    plan = {**PLAN, "source": {"branch": "main", "sha": "f" * 40}}

    response = _call(client, run, "plan", plan=plan)

    assert response.status_code == 422


def test_oversized_plan_is_refused(client, world):
    run = _run(world, _source(world))
    _call(client, run, "claim")

    response = _call(
        client, run, "plan", plan={**PLAN, "warnings": ["x" * 3_000_000]}
    )

    assert response.status_code == 413


def test_plan_finishes_the_analysis(client, world):
    run = _run(world, _source(world))
    _call(client, run, "claim")

    response = _call(client, run, "plan", plan=PLAN)

    assert response.status_code == 204
    with world.db() as db:
        stored = db.get(ImportRun, run.id)
        assert stored.status == "succeeded"
        assert len(stored.plan["services"]) == 7


def test_build_claim_lists_one_recipe_group_for_the_maven_reactor(
    client, world
):
    source = _source(world)
    specs = generate.resolve(PLAN)
    with world.db() as db:
        for spec in specs:
            db.add(
                ImportService(
                    source_id=source.id,
                    app_name=spec.name,
                    kind=spec.kind,
                    path=spec.path,
                    spec=spec.to_dict(),
                )
            )
        db.commit()
    run = _run(world, source, kind="build", plan={"plan": PLAN, "edits": {}})

    response = _call(client, run, "claim")

    recipes = response.json()["recipes"]
    groups = {r["app"]: r["group"] for r in recipes}
    assert len(recipes) == 7
    assert groups["user-service"] == groups["post-service"]
    assert groups["frontend"] != groups["user-service"]
    by_app = {r["app"]: r for r in recipes}
    assert by_app["frontend"]["dockerfile"] == "frontend/Dockerfile"
    assert by_app["user-service"]["dockerfile_text"].startswith("# Generated")
    assert (
        by_app["user-service"]["image"]
        == "ghcr.io/3-istor/imports/shop/user-service"
    )


def _build_run(world):
    source = _source(world)
    with world.db() as db:
        for name in ("api", "db"):
            db.add(
                ImportService(
                    source_id=source.id,
                    app_name=name,
                    kind="service" if name == "api" else "backing",
                    path="",
                    spec={},
                )
            )
        db.commit()
    return _run(world, source, kind="build", status="claimed")


def test_publish_refuses_an_image_outside_the_project_path(client, world):
    run = _build_run(world)

    response = _call(
        client,
        run,
        "publish",
        services=[
            {
                "app": "api",
                "image": "ghcr.io/3-istor/imports/other/api",
                "digest": DIGEST,
            }
        ],
    )

    assert response.status_code == 422


def test_publish_refuses_a_missing_service(client, world):
    run = _build_run(world)

    response = _call(client, run, "publish", services=[])

    assert response.status_code == 422


def test_publish_starts_the_deploy(client, world):
    run = _build_run(world)

    response = _call(
        client,
        run,
        "publish",
        services=[
            {
                "app": "api",
                "image": "ghcr.io/3-istor/imports/shop/api",
                "digest": DIGEST,
                "vulns": {"critical": 0, "high": 2},
            }
        ],
    )

    assert response.status_code == 202
    with world.db() as db:
        stored = db.get(ImportRun, run.id)
        assert stored.status == "running"
        assert stored.report["images"]["api"]["digest"] == DIGEST


def test_failure_is_recorded_redacted_and_only_once(client, world):
    run = _run(world, _source(world), status="claimed")

    first = _call(
        client,
        run,
        "fail",
        stage="build",
        message="push denied for ghs_abcdefghijklmnopqrstuvwxyz0123",
    )
    second = _call(client, run, "fail", stage="build", message="again")

    assert first.status_code == 204
    assert second.status_code == 409
    with world.db() as db:
        report = db.get(ImportRun, run.id).report
    assert report["stage"] == "build"
    assert "ghs_" not in report["error"]


# ── Security review ─────────────────────────────────────────────────────────


def _applied(world):
    """An import whose build succeeded, ready to rebuild."""
    source = _source(world)
    _run(world, source, status="succeeded", plan=PLAN)
    with world.db() as db:
        db.add(
            ImportService(
                source_id=source.id,
                app_name="api",
                kind="service",
                path="",
                spec={},
            )
        )
        db.commit()
    _run(
        world,
        source,
        kind="build",
        status="succeeded",
        plan={"plan": PLAN, "edits": {}},
    )
    return source


def test_rebuild_needs_step_up(client, world, monkeypatch):
    monkeypatch.setattr(settings, "STEP_UP_ENABLED", True)
    source = _applied(world)

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert response.status_code == 401


def test_rebuild_starts_a_build_while_the_caller_can_import_the_repo(
    client, world
):
    source = _applied(world)

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert response.status_code == 202
    assert world.dispatched[-1]["kind"] == "build"


def test_rebuild_is_refused_once_the_caller_lost_the_repository(client, world):
    source = _applied(world)
    world.repos = []

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert response.status_code == 403
    assert world.dispatched == []


def test_rebuild_is_refused_when_the_name_now_holds_another_repository(
    client, world
):
    source = _applied(world)
    world.repos = [RepoRef(REPO.full_name, 4242, 7, "main", True)]

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert response.status_code == 403


def test_rebuild_is_refused_once_the_caller_is_no_longer_project_admin(
    client, world
):
    source = _applied(world)
    world.roles["shop"] = ProjectRole.MEMBER

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert response.status_code == 403


def test_apply_is_refused_once_the_caller_lost_the_repository(client, world):
    source = _source(world)
    analysis = _run(world, source, status="succeeded", plan=PLAN)
    world.repos = []

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={},
    )

    assert response.status_code == 403


def test_claim_gives_the_repository_id_and_binds_the_run_attempt(
    client, world
):
    run = _run(world, _source(world))

    response = _call(client, run, "claim")

    assert response.json()["repository_id"] == 42
    with world.db() as db:
        assert db.get(ImportRun, run.id).workflow_run_attempt == "1"


def test_a_rerun_attempt_of_the_workflow_run_cannot_act_on_the_run(
    client, world
):
    run = _run(world, _source(world))
    _call(client, run, "claim")
    world.claims = {"run_id": "555", "run_attempt": "2"}

    response = _call(client, run, "fail", stage="build", message="x")

    assert response.status_code == 403


def test_plan_over_the_app_cap_is_refused(client, world, monkeypatch):
    monkeypatch.setattr(settings, "IMPORT_MAX_APPS", 10)
    run = _run(world, _source(world))
    _call(client, run, "claim")

    response = _call(client, run, "plan", plan=PLAN)

    assert response.status_code == 422
    assert "at most 10" in response.json()["detail"]


def test_plan_env_values_that_look_like_credentials_are_dropped(client, world):
    run = _run(world, _source(world))
    _call(client, run, "claim")
    plan = json.loads(json.dumps(PLAN))
    plan["services"][0]["env"] += [
        {"name": "UPSTREAM_URL", "value": "https://bob:hunter2@api.example"},
        {"name": "GH", "value": "ghp_" + "a1B2" * 9},
        {"name": "LOG_LEVEL", "value": "info"},
    ]

    _call(client, run, "plan", plan=plan)

    with world.db() as db:
        stored = db.get(ImportRun, run.id).plan
    env = {e["name"]: e for e in stored["services"][0]["env"]}
    assert env["UPSTREAM_URL"]["value"] is None
    assert env["UPSTREAM_URL"]["secret"] is True
    assert env["GH"]["value"] is None
    assert env["LOG_LEVEL"]["value"] == "info"
    assert any("UPSTREAM_URL" in w for w in stored["warnings"])


def test_worker_body_over_the_cap_is_refused_before_parsing(
    client, world, monkeypatch
):
    monkeypatch.setattr(settings, "IMPORT_WORKER_MAX_BODY_BYTES", 1024)
    run = _run(world, _source(world))
    _call(client, run, "claim")

    response = _call(client, run, "fail", stage="build", message="x" * 5000)

    assert response.status_code == 413


def test_worker_body_without_content_length_is_capped_while_streamed(
    client, world, monkeypatch
):
    monkeypatch.setattr(settings, "IMPORT_WORKER_MAX_BODY_BYTES", 1024)
    run = _run(world, _source(world))

    def chunks():
        yield b'{"attempt_id": "'
        yield b"x" * 4096
        yield b'"}'

    response = client.post(
        f"/api/import/worker/{run.id}/claim", content=chunks()
    )

    assert response.status_code == 413


def test_unauthenticated_worker_call_is_refused_before_its_body_is_read(
    client, world, monkeypatch
):
    del client.app.dependency_overrides[imports_router.worker_claims]
    run = _run(world, _source(world))
    read = []
    monkeypatch.setattr(
        imports_router, "_read_body", lambda *a: read.append(a)
    )

    response = client.post(
        f"/api/import/worker/{run.id}/plan",
        content=b"{" + b" " * 3_000_000 + b"}",
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 401
    assert read == []


# ── Re-analysis, refusals that point at the latest run, stuck runs ──────────


@pytest.fixture
def reanalyze(client, world, monkeypatch):
    async def resolve(db, claims, name):
        return REPO

    monkeypatch.setattr(access, "resolve_repository", resolve)

    def post(branch="dev"):
        return client.post(
            "/api/projects/shop/imports",
            json={"repo_full_name": REPO.full_name, "branch": branch},
        )

    return post


def _branch(world, source) -> str:
    with world.db() as db:
        return db.get(ImportSource, source.id).branch


def _stuck_since(status: str, minutes_past_timeout: int) -> datetime:
    return service._now() - timedelta(
        minutes=service._timeouts()[status] + minutes_past_timeout
    )


def test_a_re_analysis_refused_while_a_run_is_in_progress_keeps_the_branch(
    world, reanalyze
):
    source = _source(world)
    _run(world, source, kind="build", status="claimed")

    response = reanalyze()

    assert (response.status_code, _branch(world, source)) == (409, "main")


def test_a_re_analysis_that_loses_the_race_for_the_run_keeps_the_branch(
    world, reanalyze, monkeypatch
):
    source = _source(world)

    async def head_while_another_run_starts(source):
        _run(world, source, kind="build", status="queued")
        return SHA

    monkeypatch.setattr(service, "_head", head_while_another_run_starts)

    response = reanalyze()

    assert (response.status_code, _branch(world, source)) == (409, "main")


def test_a_re_analysis_of_an_unreadable_branch_keeps_the_branch(
    world, reanalyze, monkeypatch
):
    source = _source(world)

    async def unreadable(source):
        raise service.ImportRefused("Could not read branch dev", 502)

    monkeypatch.setattr(service, "_head", unreadable)

    response = reanalyze()

    assert (response.status_code, _branch(world, source)) == (502, "main")


def test_a_re_analysis_whose_workflow_cannot_start_keeps_the_branch(
    world, reanalyze, monkeypatch
):
    source = _source(world)

    async def dispatch_fails(db, run):
        run.status = "failed"
        db.commit()
        raise service.ImportRefused("Could not start the workflow", 502)

    monkeypatch.setattr(service, "_dispatch", dispatch_fails)

    response = reanalyze()

    assert (response.status_code, _branch(world, source)) == (502, "main")


def test_a_re_analysis_stores_the_new_branch_with_its_run(world, reanalyze):
    source = _source(world)

    response = reanalyze()

    assert (response.status_code, _branch(world, source)) == (202, "dev")


def test_a_refused_re_analysis_points_at_the_run_in_progress(world, reanalyze):
    source = _source(world)
    active = _run(world, source, kind="build", status="claimed")

    detail = reanalyze().json()["detail"]

    assert detail["latest_run"] == {
        "run_id": active.id,
        "kind": "build",
        "status": "claimed",
        "api_path": f"/api/projects/shop/imports/runs/{active.id}",
        "ui_path": f"/projects/shop/import?run={active.id}",
    }


def test_apply_of_a_stale_analysis_points_at_the_latest_one(client, world):
    source = _source(world)
    reviewed = _run(world, source, status="succeeded", plan=PLAN)
    latest = _run(world, source, status="succeeded", plan=PLAN)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={reviewed.id}",
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["latest_run"]["run_id"] == latest.id


def test_apply_while_a_re_analysis_runs_points_at_it(client, world):
    source = _source(world)
    reviewed = _run(world, source, status="succeeded", plan=PLAN)
    running = _run(world, source, status="claimed")

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={reviewed.id}",
        json={},
    )

    assert response.json()["detail"]["latest_run"]["run_id"] == running.id


@pytest.mark.parametrize("status", ["queued", "claimed", "running"])
def test_a_run_without_news_past_the_timeout_no_longer_blocks_an_analysis(
    world, reanalyze, status
):
    source = _source(world)
    stuck = _run(
        world,
        source,
        kind="build",
        status=status,
        updated_at=_stuck_since(status, 1),
    )

    response = reanalyze()

    assert response.status_code == 202
    with world.db() as db:
        failed = db.get(ImportRun, stuck.id)
        assert (failed.status, failed.report["stage"]) == ("failed", "timeout")


@pytest.mark.parametrize("status", ["queued", "claimed", "running"])
def test_a_run_within_the_timeout_still_blocks_an_analysis(
    world, reanalyze, status
):
    source = _source(world)
    _run(world, source, status=status, updated_at=_stuck_since(status, -1))

    response = reanalyze()

    assert response.status_code == 409


def test_a_claimed_build_outlives_the_queue_timeout(world, reanalyze):
    source = _source(world)
    _run(
        world,
        source,
        kind="build",
        status="claimed",
        updated_at=_stuck_since("queued", 1),
    )

    response = reanalyze()

    assert response.status_code == 409


def test_a_run_whose_deploy_runs_in_this_process_is_never_expired(
    world, reanalyze, monkeypatch
):
    source = _source(world)
    deploying = _run(
        world,
        source,
        kind="build",
        status="running",
        updated_at=_stuck_since("running", 1),
    )
    monkeypatch.setattr(service, "_deploying", {deploying.id})

    response = reanalyze()

    assert response.status_code == 409


def _rebuild_user(world, source) -> str | None:
    with world.db() as db:
        return db.get(ImportSource, source.id).rebuild_user


def test_a_refused_rebuild_leaves_the_rebuild_user_unchanged(client, world):
    source = _applied(world)
    with world.db() as db:
        db.get(ImportSource, source.id).rebuild_user = "bob"
        db.commit()
    _run(world, source, kind="build", status="claimed")

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert (response.status_code, _rebuild_user(world, source)) == (409, "bob")


def test_a_refused_apply_leaves_the_rebuild_user_unchanged(client, world):
    source = _source(world, rebuild_user="bob")
    reviewed = _run(world, source, status="succeeded", plan=PLAN)
    _run(world, source, status="succeeded", plan=PLAN)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={reviewed.id}",
        json={},
    )

    assert (response.status_code, _rebuild_user(world, source)) == (409, "bob")


def test_an_applied_import_rebuilds_as_the_admin_who_applied_it(client, world):
    source = _source(world, rebuild_user="bob")
    analysis = _run(world, source, status="succeeded", plan=PLAN)

    response = client.post(
        f"/api/projects/shop/imports/{source.id}/apply?run_id={analysis.id}",
        json={},
    )

    assert (response.status_code, _rebuild_user(world, source)) == (
        202,
        "alice",
    )


def test_a_stuck_run_no_longer_blocks_a_rebuild(client, world):
    source = _applied(world)
    _run(
        world,
        source,
        kind="build",
        status="running",
        updated_at=_stuck_since("running", 1),
    )

    response = client.post(f"/api/projects/shop/imports/{source.id}/rebuild")

    assert response.status_code == 202
