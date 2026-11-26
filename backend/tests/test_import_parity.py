"""Imported apps on the Day-2 screens: repo guard, checklist, security, OTEL."""

from types import SimpleNamespace
from unittest import mock

import pytest

from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.imports import ImportService, ImportSource
from app.routers import deployments as deployments_router
from app.services import app_repo, vault_client
from app.services.imports import generate, tasks
from app.services.imports.generate import AppSpec
from app.services.security import collector
from tests.test_app_security_data import (  # noqa: F401
    FakeRepo,
    make_client,
    session_factory,
)

SWITCHED = {"state": "switched", "switched": ["web"], "url": "https://pr/1"}


def add_import(
    factory,
    standard_repo: dict | None = None,
    repo: str = "3-Istor/shop",
    spec: dict | None = None,
) -> int:
    with factory() as db:
        deployment = Deployment(
            name="web",
            template_id="import-service",
            provider_type=ProviderType.KUBERNETES,
            project_id="demo",
            status=DeploymentStatus.RUNNING,
            github_repo_url=f"https://github.com/{repo}",
            k8s_namespace="demo-web",
        )
        db.add(deployment)
        db.flush()
        source = ImportSource(
            project_name="demo",
            repo_full_name=repo,
            repo_id=42,
            installation_id=7,
            branch="trunk",
            created_by="u",
            standard_repo=standard_repo,
        )
        source.services.append(
            ImportService(
                app_name="web",
                kind="service",
                spec=spec
                or {"name": "web", "kind": "service", "public": True},
                deployment_id=deployment.id,
            )
        )
        db.add(source)
        db.commit()
        return deployment.id


@pytest.fixture
def import_client(session_factory):  # noqa: F811
    started = []

    def build(files: dict[str, str], role: str, **source):
        deployment_id = add_import(session_factory, **source)
        repo = FakeRepo(files)
        client, patches = make_client(session_factory, repo, role)
        scoped = mock.patch.object(
            deployments_router,
            "get_repository_id_scoped_token",
            mock.AsyncMock(return_value="scoped"),
        )
        scoped_token = scoped.start()
        started.extend([*patches, scoped])
        return client, repo, deployment_id, scoped_token

    yield build
    for patch in started:
        patch.stop()


# ── Guard ────────────────────────────────────────────────────────────────────


def test_import_not_switched_is_not_managed(session_factory):  # noqa: F811
    deployment_id = add_import(session_factory, {"state": "open"})

    with session_factory() as db, pytest.raises(app_repo.RepoNotManaged):
        app_repo.import_binding(db, db.get(Deployment, deployment_id))


def test_switched_import_binds_its_deploy_values_file(
    session_factory,  # noqa: F811
):
    deployment_id = add_import(session_factory, SWITCHED)

    with session_factory() as db:
        binding = app_repo.import_binding(
            db, db.get(Deployment, deployment_id)
        )

    assert binding == app_repo.ImportBinding(
        repo="3-Istor/shop",
        repo_id=42,
        installation_id=7,
        branch="trunk",
        values_file="deploy/values-web.yaml",
    )


def test_switched_import_outside_the_org_is_never_managed(
    session_factory,  # noqa: F811
):
    deployment_id = add_import(session_factory, SWITCHED, repo="acme/shop")

    with session_factory() as db, pytest.raises(app_repo.RepoNotManaged):
        app_repo.import_binding(db, db.get(Deployment, deployment_id))


def test_app_kept_on_the_platform_by_the_switch_is_not_managed(
    session_factory,  # noqa: F811
):
    deployment_id = add_import(
        session_factory, {"state": "switched", "switched": ["api"]}
    )

    with session_factory() as db, pytest.raises(app_repo.RepoNotManaged):
        app_repo.import_binding(db, db.get(Deployment, deployment_id))


def test_config_of_an_unmanaged_import_answers_the_repo_not_managed_code(
    import_client,
):
    client, _, deployment_id, _ = import_client({}, "member")

    response = client.get(f"/api/deployments/{deployment_id}/config")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "repo_not_managed"


def test_config_of_a_switched_import_reads_its_values_with_a_scoped_token(
    import_client,
):
    client, _, deployment_id, scoped = import_client(
        {"deploy/values-web.yaml": "replicaCount: 2\n"},
        "member",
        standard_repo=SWITCHED,
    )

    response = client.get(f"/api/deployments/{deployment_id}/config")

    assert response.status_code == 200
    assert response.json()["file_path"] == "deploy/values-web.yaml"
    assert scoped.await_args.args[:2] == ("7", [42])


def test_config_write_of_a_switched_import_commits_its_values_file(
    import_client,
):
    client, repo, deployment_id, _ = import_client(
        {"deploy/values-web.yaml": "replicaCount: 2\n"},
        "admin",
        standard_repo=SWITCHED,
    )

    response = client.patch(
        f"/api/deployments/{deployment_id}/config",
        json={"_sha": "sha-deploy/values-web.yaml", "replicaCount": 3},
    )

    assert response.status_code == 200
    assert [path for path, _, _ in repo.commits] == ["deploy/values-web.yaml"]


def test_security_data_of_an_unmanaged_import_shows_the_cluster_route(
    import_client,
):
    client, _, deployment_id, _ = import_client({}, "admin")

    with mock.patch.object(
        deployments_router, "kube_list", return_value=[{"kind": "HTTPRoute"}]
    ):
        response = client.get(
            f"/api/deployments/{deployment_id}/security-data"
        )

    assert response.json() == {
        "repo": None,
        "can_edit": False,
        "managed": False,
        "exposure": "public",
        "database": None,
    }


def test_staging_stays_refused_for_a_switched_import(
    session_factory,  # noqa: F811
):
    deployment_id = add_import(session_factory, SWITCHED)

    with session_factory() as db, mock.patch.object(
        deployments_router,
        "_get_app_or_404",
        lambda db, token, i, role, cap=None: db.get(Deployment, i),
    ):
        with pytest.raises(deployments_router.HTTPException) as refused:
            deployments_router._get_kubernetes_deployment_or_404(
                deployment_id, db, {}, None
            )

    assert refused.value.detail["code"] == "import_unsupported"


def test_secrets_of_an_import_are_checked_from_its_spec_without_github(
    session_factory,  # noqa: F811
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.routers import secrets_presence
    from app.services.keycloak_service import get_current_user

    deployment_id = add_import(
        session_factory,
        spec={
            "name": "web",
            "kind": "service",
            "secret_env": [{"env": "API_KEY", "backing": "", "key": "x"}],
        },
    )
    app = FastAPI()
    app.include_router(secrets_presence.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    with mock.patch.object(
        deployments_router,
        "_get_app_or_404",
        lambda db, token, i, role, cap=None: db.get(Deployment, i),
    ), mock.patch.object(
        vault_client, "read_key_names", mock.AsyncMock(return_value=[])
    ) as read:
        response = TestClient(app).get(
            f"/api/deployments/{deployment_id}/secrets/presence"
        )

    assert response.json()["expected"] == [{"name": "API_KEY", "set": False}]
    assert read.await_args.args == ("project-demo", "web/prod/secrets")


# ── Checklist ────────────────────────────────────────────────────────────────


def _source(standard_repo=None, repo="3-Istor/shop") -> ImportSource:
    return ImportSource(
        id="s1",
        project_name="demo",
        repo_full_name=repo,
        standard_repo=standard_repo,
        source_mode="in_place",
    )


def _service(kind="service", public=True) -> ImportService:
    return ImportService(app_name="web", kind=kind, spec={"public": public})


ALL_DONE = tasks.AppState(
    running=True, route_live=True, code_scanned=True, has_traces=True
)
NOTHING = tasks.AppState(
    running=False, route_live=False, code_scanned=False, has_traces=False
)


def test_a_fresh_public_service_has_five_tasks_to_do():
    found = tasks.compute(_source({"state": "open"}), _service(), NOTHING)

    assert [(t.key, t.done) for t in found] == [
        ("deploy", False),
        ("route", False),
        ("setup_pr", False),
        ("code_scanning", False),
        ("tracing", False),
    ]


def test_an_open_setup_pr_is_the_action_of_the_setup_task():
    source = _source({"state": "open", "url": "https://pr/9"})

    setup = tasks.compute(source, _service(), NOTHING)[2]

    assert setup.action == {"kind": "pr", "href": "https://pr/9"}


def test_a_switched_import_has_every_task_done():
    found = tasks.compute(_source(SWITCHED), _service(), ALL_DONE)

    view = tasks.view(_source(SWITCHED), _service(), found)

    assert (view["done"], view["total"], view["managed"]) == (5, 5, True)


def test_a_private_backing_app_only_tracks_deploy_and_setup():
    found = tasks.compute(_source(), _service("backing", False), NOTHING)

    assert [t.key for t in found] == ["deploy", "setup_pr"]


def test_tracing_is_left_out_when_the_traces_store_is_off():
    state = tasks.AppState(
        running=True, route_live=True, code_scanned=False, has_traces=None
    )

    found = tasks.compute(_source(), _service(), state)

    assert "tracing" not in [t.key for t in found]


def test_setup_task_of_a_foreign_repository_says_why_it_is_blocked():
    source = _source(repo="acme/shop")

    setup = tasks.compute(source, _service(), NOTHING)[2]

    assert "not a repository of" in setup.params["blocked"]


def test_route_task_names_the_app_hostname():
    route = tasks.compute(_source(), _service(), NOTHING)[1]

    assert route.view()["label"] == (
        f"Route public traffic ({generate.hostname('demo', 'web')})"
    )


def test_remaining_scanning_and_tracing_go_to_an_agent_once_switched():
    found = tasks.compute(_source(SWITCHED), _service(), NOTHING)

    assert {t.key: t.action["kind"] for t in found[3:]} == {
        "code_scanning": "agent",
        "tracing": "agent",
    }


# ── Security ─────────────────────────────────────────────────────────────────


def test_collector_scans_an_unmanaged_import_in_the_cluster_only(
    session_factory,  # noqa: F811
):
    add_import(session_factory, {"state": "open"})

    with session_factory() as db:
        entry = collector.kubernetes_projects(db)[0]

    app = entry.apps[0]
    assert (app.namespace, app.repo, entry.imported) == (
        "demo-web",
        None,
        frozenset({"web"}),
    )
    assert app.owns_image("3-istor/imports/demo/web")


def test_collector_reads_the_repository_of_a_switched_import(
    session_factory,  # noqa: F811
):
    add_import(session_factory, SWITCHED)

    with session_factory() as db:
        entry = collector.kubernetes_projects(db)[0]

    assert entry.apps[0].repo == "3-Istor/shop"


def test_github_pass_skips_an_unmanaged_import(session_factory):  # noqa: F811
    entry = collector.ProjectApps(
        project="demo",
        apps=[collector.sources.AppRef("web", "demo-web", None)],
        deployments={},
        imported=frozenset({"web"}),
    )
    github = mock.AsyncMock(return_value=[])

    with session_factory() as db, mock.patch.object(
        collector, "collect_github_app", github
    ), mock.patch.object(
        collector, "project_policy", mock.AsyncMock(return_value=None)
    ), mock.patch.object(
        collector.alerts, "known_scans", lambda db, p: set()
    ), mock.patch.object(
        collector.alerts, "to_notify", lambda *a: []
    ):
        import asyncio

        asyncio.run(collector.collect_project(db, entry, False, True))

    github.assert_not_awaited()


# ── OTEL ─────────────────────────────────────────────────────────────────────


def _values(env: list[dict]) -> dict:
    spec = AppSpec(
        name="web",
        kind="service",
        env=env,
        ports=[{"name": "http", "port": 8080}],
    )
    return generate.service_values(
        "demo", spec, "ghcr.io/x", "sha256:" + "a" * 64
    )


def test_imported_service_gets_the_platform_tracing_env():
    assert _values([])["tracing"] == {"enabled": True}


def test_an_app_that_sets_its_own_otel_resource_keeps_tracing_off():
    values = _values([{"name": "OTEL_RESOURCE_ATTRIBUTES", "value": "a=b"}])

    assert "tracing" not in values


def test_namespace_fallback_matches_the_import_convention():
    deployment = SimpleNamespace(k8s_namespace=None, project_id="p", name="a")

    with mock.patch.object(
        deployments_router, "kube_list", return_value=[]
    ) as listed, mock.patch.object(
        deployments_router, "project_cloud", lambda db, p: "onprem"
    ):
        deployments_router._cluster_exposure(None, deployment)

    assert "/namespaces/p-a/httproutes" in listed.call_args.args[0]
