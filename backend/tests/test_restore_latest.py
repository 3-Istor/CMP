import json
from datetime import datetime, timezone
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import TargetCloud
from app.models.project_move import ProjectMove
from app.routers import (  # noqa: F401  (import order)
    deployments as _deployments,
)
from app.routers import security as security_router
from app.routers import security_databases as router
from app.services.clusters.state import not_connected_response
from app.services.keycloak_service import get_current_user
from app.services.kube_client import ClusterNotConnectedError
from app.services.security import restore_commit

BACKEND = "deploy/values-backend.yaml"
VALUES = "db:\n  enabled: true\n  backup:\n    enabled: true\n"


def _cluster(name, backups=True):
    plugins = [{"name": "barman-cloud.cloudnative-pg.io"}] if backups else []
    return {"metadata": {"name": name}, "spec": {"plugins": plugins}}


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(
            Deployment(
                name="api",
                template_id="k3s-gitops-app",
                provider_type=ProviderType.KUBERNETES,
                project_id="shop",
                status=DeploymentStatus.RUNNING,
                github_repo_url="https://github.com/3-istor/api",
                app_config=json.dumps({"app_type": "fullstack"}),
            )
        )
        db.commit()

    commits = []

    async def put(token, repo, path, content, message, sha, branch):
        commits.append((path, message, content))
        return {"commit": {"sha": "c1"}}

    async def get(*_args):
        return VALUES, "sha"

    monkeypatch.setattr(restore_commit, "get_file_content", get)
    monkeypatch.setattr(restore_commit, "put_file_content", put)
    monkeypatch.setattr(
        restore_commit,
        "get_default_branch",
        mock.AsyncMock(return_value="main"),
    )
    monkeypatch.setattr(
        restore_commit,
        "_github_token",
        mock.AsyncMock(return_value=("t", "r")),
    )

    app = FastAPI()
    app.include_router(router.router)
    app.add_exception_handler(ClusterNotConnectedError, not_connected_response)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"groups": ["infra"]}
    return TestClient(app), commits, monkeypatch, app


def _post(client, **kw):
    return client.post(
        "/security/restores/latest",
        json={"project": "shop", "app": "api"},
        **kw
    )


def test_restore_latest_commits_patch(setup):
    client, commits, mp, _ = setup
    mp.setattr(
        router, "kube_list", lambda *a, **k: [_cluster("shop-api-backend-db")]
    )

    response = _post(client)

    assert response.status_code == 200
    ((path, message, content),) = commits
    assert path == BACKEND
    assert "restore to latest" in message
    assert "sourceServer: shop-api-backend-db" in content


def test_restore_latest_picks_the_current_generation(setup):
    client, commits, mp, _ = setup
    names = ["shop-api-backend-db", "shop-api-backend-db-r2"]
    mp.setattr(
        router, "kube_list", lambda *a, **k: [_cluster(n) for n in names]
    )
    values = VALUES + "  restore:\n    generation: 2\n"

    async def get(*_args):
        return values, "sha"

    mp.setattr(restore_commit, "get_file_content", get)

    assert _post(client).status_code == 200
    assert "sourceServer: shop-api-backend-db-r2" in commits[0][2]


def test_restore_latest_dry_run_returns_the_diff(setup):
    client, commits, mp, _ = setup
    mp.setattr(
        router, "kube_list", lambda *a, **k: [_cluster("shop-api-backend-db")]
    )

    response = _post(client, params={"dry_run": "true"})

    assert response.status_code == 200
    assert "+    sourceServer: shop-api-backend-db" in response.json()["diff"]
    assert commits == []


def test_restore_latest_refuses_without_backups(setup):
    client, commits, mp, _ = setup
    mp.setattr(
        router, "kube_list", lambda *a, **k: [_cluster("x-db", backups=False)]
    )

    assert _post(client).status_code == 409
    assert commits == []


def test_restore_latest_refuses_when_cloud_down(setup):
    client, commits, mp, _ = setup

    def down(*_a, **_k):
        raise ClusterNotConnectedError(TargetCloud.AWS)

    mp.setattr(router, "kube_list", down)

    response = _post(client)

    assert response.status_code == 503
    assert response.json()["detail"] == "cluster_not_connected"
    assert commits == []


def test_restore_latest_requires_admin(setup):
    client, commits, mp, app = setup

    def deny(*_a):
        raise HTTPException(status_code=403)

    app.dependency_overrides[get_current_user] = lambda: {"groups": []}
    mp.setattr(security_router, "require_project_member", deny)
    mp.setattr(router, "kube_list", lambda *a, **k: [_cluster("x-db")])

    assert _post(client).status_code == 403
    assert commits == []


@pytest.mark.parametrize(
    "path,body",
    [
        ("/security/restores/latest", {"project": "shop", "app": "api"}),
        (
            "/security/restores",
            {"project": "shop", "app": "api", "backup": "b"},
        ),
        ("/security/backups", {"project": "shop", "app": "api"}),
    ],
)
def test_backups_and_restores_wait_for_a_move(setup, path, body):
    client, commits, mp, app = setup
    db = next(app.dependency_overrides[get_db]())
    db.add(
        ProjectMove(
            project="shop",
            source="aws",
            target="gcp",
            status="failed",
            steps=[],
            actor="hugo",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    mp.setattr(router, "kube_list", lambda *a, **k: [_cluster("x-db")])

    response = client.post(path, json=body)

    assert response.status_code == 409
    assert response.json()["detail"] == "This project is moving."
    assert commits == []
