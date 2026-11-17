from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project
from app.routers import deployments as deployments_router
from app.routers import projects as projects_router
from app.services import project_health
from app.services.keycloak_service import get_current_user
from app.services.kube_client import KubeUnavailableError
from app.services.observability import stores
from app.services.observability.stores import StoreError

ADMIN = {"preferred_username": "adm", "groups": ["/infra"]}
MEMBER = {"preferred_username": "mem", "groups": []}


def _app(project, app, component, health, sync="Synced"):
    return {
        "metadata": {
            "name": f"{project}-{app}-{component}-prod",
            "labels": {
                "cnp.3istor.com/project": project,
                "cnp.3istor.com/app": app,
            },
        },
        "status": {"health": {"status": health}, "sync": {"status": sync}},
    }


def _row(i, name, project, status=DeploymentStatus.RUNNING):
    return Deployment(
        id=i,
        name=name,
        template_id="k3s-gitops-app",
        project_id=project,
        provider_type=ProviderType.KUBERNETES,
        status=status,
    )


@pytest.fixture(autouse=True)
def fresh_cache():
    project_health._cache = None
    yield
    project_health._cache = None


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all(
            [
                Project(project_name="shop", owner_username="adm"),
                Project(project_name="other", owner_username="adm"),
                _row(1, "web", "shop"),
                _row(2, "site", "shop"),
                _row(3, "gone", "shop", DeploymentStatus.DELETED),
                _row(4, "x", "other"),
            ]
        )
        db.commit()
    return factory


@pytest.fixture
def kube():
    with mock.patch.object(project_health, "kube_list") as kube_list:
        kube_list.return_value = [
            _app("shop", "web", "app", "Healthy"),
            _app("shop", "site", "app", "Degraded"),
            _app("other", "x", "app", "Healthy"),
        ]
        yield kube_list


@pytest.fixture
def alerts():
    with mock.patch.object(
        stores, "firing_by_project", new=mock.AsyncMock(return_value={})
    ) as firing:
        yield firing


def _get(session_factory, token=None):
    token = token or ADMIN
    app = FastAPI()
    app.include_router(projects_router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: token
    response = TestClient(app).get("/api/projects/health")
    assert response.status_code == 200
    return {p["project"]: p for p in response.json()}


def test_health_counts_apps_by_their_argo_health(
    session_factory, kube, alerts
):
    shop = _get(session_factory)["shop"]

    assert (shop["apps"], shop["healthy"], shop["down"]) == (2, 1, 1)
    assert shop["health"] == "degraded"
    assert shop["sync"] == "synced"


def test_an_app_shows_its_worst_component(session_factory, kube, alerts):
    kube.return_value = [
        _app("shop", "web", "backend", "Healthy"),
        _app("shop", "web", "frontend", "Progressing"),
    ]

    shop = _get(session_factory)["shop"]

    assert (shop["healthy"], shop["down"]) == (0, 0)
    assert shop["health"] == "progressing"


def test_applications_are_read_once_for_prod_only(
    session_factory, kube, alerts
):
    _get(session_factory)

    kube.assert_called_once_with(
        project_health.APPLICATIONS_PATH,
        {
            "labelSelector": "cnp.3istor.com/project,cnp.3istor.com/app,"
            "cnp.3istor.com/env=prod"
        },
    )


def test_alerts_are_counted_per_project(session_factory, kube):
    alertmanager = [
        {"labels": {"cnp_project": "shop"}},
        {"labels": {"cnp_project": "shop"}},
        {"labels": {"cnp_project": "other"}},
        {"labels": {}},
    ]
    response = mock.Mock()
    response.json.return_value = alertmanager
    client = mock.AsyncMock()
    client.get.return_value = response
    client.__aenter__.return_value = client
    with (
        mock.patch.object(stores, "require"),
        mock.patch.object(stores, "_client", return_value=client),
    ):
        body = _get(session_factory)

    assert (body["shop"]["alerts"], body["other"]["alerts"]) == (2, 1)
    params = client.get.call_args.kwargs["params"]
    assert ("silenced", "false") in params and ("active", "true") in params


def test_only_the_callers_projects_are_listed(session_factory, kube, alerts):
    with mock.patch.object(
        deployments_router, "_user_project_names", return_value=["shop"]
    ):
        body = _get(session_factory, MEMBER)

    assert list(body) == ["shop"]


def test_unreadable_argo_leaves_health_unknown(session_factory, kube):
    kube.side_effect = KubeUnavailableError("down")
    with mock.patch.object(
        stores,
        "firing_by_project",
        new=mock.AsyncMock(return_value={"shop": 3}),
    ):
        shop = _get(session_factory)["shop"]

    assert (shop["health"], shop["sync"]) == (None, None)
    assert (shop["healthy"], shop["down"], shop["alerts"]) == (0, 0, 3)


def test_unreadable_alertmanager_leaves_alerts_unknown(session_factory, kube):
    with mock.patch.object(
        stores,
        "firing_by_project",
        new=mock.AsyncMock(side_effect=StoreError("alerts: down")),
    ):
        shop = _get(session_factory)["shop"]

    assert shop["alerts"] is None
    assert shop["health"] == "degraded"


def test_the_snapshot_is_cached(session_factory, kube, alerts):
    _get(session_factory)
    _get(session_factory)

    assert kube.call_count == 1


def test_a_failed_app_without_an_application_is_down(session_factory, alerts):
    with session_factory() as db:
        db.add(_row(5, "broken", "shop", DeploymentStatus.FAILED))
        db.commit()
    with mock.patch.object(project_health, "kube_list") as kube_list:
        kube_list.return_value = [_app("shop", "web", "app", "Healthy")]
        shop = _get(session_factory)["shop"]

    # web healthy, site not in Argo yet (pending), broken failed (down).
    assert (shop["apps"], shop["healthy"], shop["down"]) == (3, 1, 1)
    assert shop["health"] == "degraded"


def test_an_application_without_a_row_is_ignored(session_factory, alerts):
    with mock.patch.object(project_health, "kube_list") as kube_list:
        kube_list.return_value = [
            _app("shop", "web", "app", "Healthy"),
            _app("shop", "site", "app", "Healthy"),
            _app("shop", "leftover", "app", "Healthy"),
        ]
        shop = _get(session_factory)["shop"]

    assert (shop["apps"], shop["healthy"], shop["down"]) == (2, 2, 0)


def test_a_guest_sees_the_health_of_its_projects(
    session_factory, kube, alerts
):
    with mock.patch.object(
        deployments_router, "_user_project_names", return_value=["shop"]
    ):
        body = _get(session_factory, {"preferred_username": "vie"})

    assert list(body) == ["shop"]
