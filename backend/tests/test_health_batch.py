import threading
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.routers import infra
from app.services import app_health, monitoring_service
from app.services.keycloak_service import get_current_user

K8S = ProviderType.KUBERNETES


def _row(id_, project="shop", provider=K8S, status=DeploymentStatus.RUNNING):
    return Deployment(
        id=id_,
        name=f"app{id_}",
        template_id="k3s",
        project_id=project,
        provider_type=provider,
        status=status,
    )


@pytest.fixture
def client():
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
                _row(1),
                _row(2),
                _row(3, project="other"),
                _row(4, provider=ProviderType.LEGACY_HYBRID),
                _row(5, status=DeploymentStatus.DELETED),
            ]
        )
        db.commit()

    def _db():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(infra.router, prefix="/api")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


def _health(deployment, now):
    return monitoring_service.AppHealthResponse(
        deployment_name=deployment.name,
        status="healthy",
        aws_frontend=None,
        openstack_backend=None,
    )


URL = "/api/infra/deployments/health?ids=1&ids=2&ids=3&ids=4&ids=5&ids=99"


@pytest.fixture
def setup(monkeypatch):
    """Patch the caller's rights and the per-app read; returns the check mock."""

    def _setup(admin=False, check=None, read=_health):
        check = check or mock.Mock()
        monkeypatch.setattr(infra, "is_cnp_admin", lambda _t: admin)
        monkeypatch.setattr(infra, "assert_capability", check)
        monkeypatch.setattr(app_health, "_read_app_health", read)
        return check

    return _setup


def test_batch_answers_kubernetes_non_deleted_apps_with_one_check_per_project(
    client, setup
):
    check = setup()

    body = client.get(URL).json()

    assert sorted(body) == ["1", "2", "3"]
    assert body["1"]["status"] == "healthy"
    assert sorted(c.args[2] for c in check.call_args_list) == ["other", "shop"]


def test_batch_omits_a_project_the_caller_cannot_see(client, setup):
    def check(db, token, project, minimum):
        if project == "other":
            raise HTTPException(status_code=403)

    setup(check=check)

    assert sorted(client.get(URL).json()) == ["1", "2"]


def test_batch_propagates_a_keycloak_outage(client, setup):
    setup(check=mock.Mock(side_effect=HTTPException(status_code=503)))

    assert client.get(URL).status_code == 503


def test_cnp_admin_skips_the_role_checks(client, setup):
    check = setup(admin=True)

    assert sorted(client.get(URL).json()) == ["1", "2", "3"]
    check.assert_not_called()


def test_batch_omits_an_app_whose_read_fails(client, setup):
    def read(deployment, now):
        if deployment.id == 2:
            raise RuntimeError("boom")
        return _health(deployment, now)

    setup(admin=True, read=read)

    assert sorted(client.get(URL).json()) == ["1", "3"]


def test_batch_reads_apps_concurrently(client, setup):
    barrier = threading.Barrier(3, timeout=5)

    def read(deployment, now):
        barrier.wait()  # sequential reads would never meet: broken, omitted
        return _health(deployment, now)

    setup(admin=True, read=read)

    assert sorted(client.get(URL).json()) == ["1", "2", "3"]


def test_batch_omits_project_less_apps_for_non_admins(client, setup):
    factory_db = next(client.app.dependency_overrides[get_db]())
    factory_db.add(_row(6, project=None))
    factory_db.commit()
    url = "/api/infra/deployments/health?ids=1&ids=6"

    setup(admin=False)
    assert sorted(client.get(url).json()) == ["1"]

    setup(admin=True)
    assert sorted(client.get(url).json()) == ["1", "6"]


def test_batch_with_no_ids_is_empty(client, setup):
    setup()

    assert client.get("/api/infra/deployments/health").json() == {}
