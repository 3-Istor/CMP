import json
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.deployment import Deployment
from app.models.project import Project, TargetCloud
from app.routers import deployments as router
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import session_factory  # noqa: F401


@pytest.mark.parametrize(
    "cloud,sent,expected",
    [
        (TargetCloud.AWS, None, "aws"),
        (TargetCloud.ONPREM, None, "onprem"),
        (TargetCloud.AWS, "gcp", "aws"),  # client value is overridden
    ],
)
def test_app_config_gets_the_project_cloud(
    session_factory, cloud, sent, expected  # noqa: F811
):
    with session_factory() as db:
        db.add(Project(project_name="demo", owner_username="u", target_cloud=cloud))
        db.commit()

    app = FastAPI()
    app.include_router(router.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    config = {"app_type": "static"}
    if sent:
        config["target_cloud"] = sent

    with (
        mock.patch.object(router, "is_cnp_admin", lambda token: True),
        mock.patch.object(router, "ensure_app_names_free", mock.AsyncMock()),
        mock.patch.object(router.terraform_orchestrator, "run_deployment"),
    ):
        response = TestClient(app).post(
            "/api/deployments/",
            json={
                "name": "shop",
                "template_id": "k3s-gitops-app",
                "project_id": "demo",
                "app_config": config,
            },
        )

    assert response.status_code == 202
    with session_factory() as db:
        row = db.query(Deployment).one()
        assert json.loads(row.app_config)["target_cloud"] == expected
