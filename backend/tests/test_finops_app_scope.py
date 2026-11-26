from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.finops import ProjectBudget
from app.routers import finops as router
from app.services.finops.engine import AppSpec
from app.services.keycloak_service import get_current_user

# Simulated quotas are seeded by id: 2 and 3 both get recommendations.
APPS = ((2, "api", "qcm"), (3, "web", "qcm"), (4, "shop", "other"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_PROVIDER", "simulated")
    monkeypatch.setattr(router, "is_cnp_admin", lambda token: False)
    monkeypatch.setattr(router, "_user_project_names", lambda token: ["qcm"])
    monkeypatch.setattr(
        router, "_require_project_role", lambda db, token, project, *a: None
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    created = datetime.now(timezone.utc) - timedelta(days=60)
    with factory() as db:
        for app_id, name, project in APPS:
            db.add(
                Deployment(
                    id=app_id,
                    name=name,
                    template_id="k3s-gitops-app",
                    provider_type=ProviderType.KUBERNETES,
                    project_id=project,
                    status=DeploymentStatus.RUNNING,
                    created_at=created,
                )
            )
        db.add(ProjectBudget(project_name="qcm", monthly_amount_eur=100.0))
        db.commit()

    def _db():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


def test_overview_app_filter_keeps_one_app(client):
    whole = client.get("/api/finops/overview?project=qcm").json()
    body = client.get("/api/finops/overview?project=qcm&app=2").json()

    assert whole["budget"] is not None
    assert [a["app_id"] for a in body["apps"]] == [2]
    assert body["budget"] is None


def test_recommendations_app_filter_keeps_one_app(client):
    whole = client.get("/api/finops/recommendations?project=qcm").json()
    body = client.get("/api/finops/recommendations?project=qcm&app=2").json()

    assert {r["app_id"] for r in whole} == {2, 3}
    assert body and {r["app_id"] for r in body} == {2}


def test_overview_app_filter_never_reads_another_project(client):
    body = client.get("/api/finops/overview?app=4").json()

    assert body["apps"] == []


def _spy_provider(monkeypatch, provider_name):
    seen = []

    class Fake:
        def specs(self, deployments):
            return [
                AppSpec(
                    d.id,
                    d.name,
                    d.project_id,
                    d.created_at,
                    None,
                    f"{d.project_id}-{d.name}",
                )
                for d in deployments
            ]

        def app_rows(self, apps, ref=None):
            seen.extend(apps)
            return []

    monkeypatch.setattr(settings, "FINOPS_PROVIDER", provider_name)
    monkeypatch.setattr(router, "get_cost_provider", Fake)
    return seen


def test_staging_cost_rows_read_the_stg_namespace_without_project_lines(
    client, monkeypatch
):
    seen = _spy_provider(monkeypatch, "real")

    client.get("/api/finops/apps?project=qcm&env=staging")

    assert {s.namespace for s in seen} == {"qcm-api-stg", "qcm-web-stg"}
    assert all(s.project_id is None for s in seen)


def test_prod_cost_rows_keep_the_project(client, monkeypatch):
    seen = _spy_provider(monkeypatch, "real")

    client.get("/api/finops/apps?project=qcm")

    assert {s.namespace for s in seen} == {"qcm-api", "qcm-web"}
    assert {s.project_id for s in seen} == {"qcm"}


def test_simulated_staging_cost_is_empty(client):
    assert client.get("/api/finops/apps?project=qcm&env=staging").json() == []
    assert (
        client.get("/api/finops/apps?project=qcm&env=dev").status_code == 422
    )
