from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.finops import RecommendationState
from app.routers import deployments as deployments_router
from app.routers import finops as router
from app.services import github_service
from app.services.finops import engine, usage
from app.services.finops.recommendations import (
    Recommendation,
    k8s_resources,
    recommendations_for_apps,
)
from app.services.keycloak_service import get_current_user
from tests.test_app_security_data import (  # noqa: F401  (fixtures)
    FakeRepo,
    add_deployment,
    session_factory,
)

PATCH = {
    "resources": {
        "requests": {"cpu": "130m", "memory": "307Mi"},
        "limits": {"cpu": "260m", "memory": "614Mi"},
    }
}
VALUES = """\
replicaCount: 2  # keep me
resources:
  requests:
    cpu: 500m
    memory: 1Gi
ingress:
  enabled: true
"""


class _Provider:
    def specs(self, deployments):
        return deployments

    def recommendations(self, apps):
        return [
            Recommendation(
                id=f"{apps[0].id}:rightsizing",
                app_id=apps[0].id,
                app_name="shop",
                project_id="demo",
                rec_type="rightsizing",
                title="t",
                justification="j",
                current={},
                recommended={},
                monthly_saving_eur=1.0,
                confidence=80,
                effort="medium",
                patch=PATCH,
            )
        ]


def _apply(session_factory, monkeypatch, app_type, files, put=None):
    deployment_id = add_deployment(session_factory, app_type)
    repo = FakeRepo(files)
    monkeypatch.setattr(deployments_router, "get_file_content", repo.get)
    monkeypatch.setattr(github_service, "put_file_content", put or repo.put)
    monkeypatch.setattr(
        deployments_router,
        "get_org_installation_token",
        mock.AsyncMock(return_value="tok"),
    )
    monkeypatch.setattr(router, "get_cost_provider", _Provider)
    monkeypatch.setattr(router, "_require_project_role", lambda *a: None)
    app = FastAPI()
    app.include_router(router.router, prefix="/api")

    def _db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    response = TestClient(app).post(
        f"/api/finops/recommendations/{deployment_id}:rightsizing/apply"
    )
    with session_factory() as db:
        states = [s.status for s in db.query(RecommendationState)]
    return response, repo, states


def test_apply_commits_the_patch_to_the_values_file(
    session_factory, monkeypatch
):
    response, repo, states = _apply(
        session_factory, monkeypatch, "static", {"deploy/values.yaml": VALUES}
    )

    assert response.status_code == 200
    assert states == ["applied"]
    assert response.json()["commit_sha"]
    content = repo.files["deploy/values.yaml"]
    assert "cpu: 130m" in content and "memory: 614Mi" in content
    assert "replicaCount: 2  # keep me" in content
    assert "ingress:" in content


def test_apply_refuses_a_fullstack_app(session_factory, monkeypatch):
    # One values file per component: a single patch cannot size both.
    files = {
        "deploy/values-frontend.yaml": VALUES,
        "deploy/values-backend.yaml": "db:\n  enabled: true\n",
    }
    response, repo, states = _apply(
        session_factory, monkeypatch, "fullstack", files
    )

    assert response.status_code == 400
    assert not repo.commits and not states


def test_apply_reports_a_github_failure_and_stays_pending(
    session_factory, monkeypatch
):
    async def down(**_kwargs):
        raise github_service.GitHubAppError("boom")

    response, _, states = _apply(
        session_factory,
        monkeypatch,
        "static",
        {"deploy/values.yaml": VALUES},
        put=down,
    )

    assert response.status_code == 502
    assert not states


def test_apply_refuses_a_values_file_that_is_not_a_mapping(
    session_factory, monkeypatch
):
    response, repo, _ = _apply(
        session_factory, monkeypatch, "static", {"deploy/values.yaml": ""}
    )

    assert response.status_code == 500
    assert not repo.commits


def test_simulated_rightsizing_patch_keeps_requests_within_limits():
    class _Dep:
        def __init__(self, i):
            self.id = i
            self.name = f"app{i}"
            self.project_id = "p"
            self.app_config = None
            self.created_at = datetime(2026, 1, 1)

    seen = 0
    for i in range(1, 31):
        app = engine.app_spec_from_deployment(_Dep(i))
        for rec in recommendations_for_apps([app]):
            if rec.rec_type != "rightsizing":
                continue
            seen += 1
            r = k8s_resources(
                rec.recommended["cpu_cores"], rec.recommended["ram_gb"]
            )
            assert rec.patch["resources"] == {"requests": r, "limits": r}
    assert seen


def test_k8s_resources_formats_quantities():
    assert k8s_resources(0.13, 0.3) == {"cpu": "130m", "memory": "308Mi"}
    assert k8s_resources(None, 1) == {"memory": "1024Mi"}


def test_simulated_hibernation_is_not_applicable(monkeypatch):
    # The chart renders `replicaCount | default 1`: 0 would keep one pod.
    monkeypatch.setattr(usage, "is_inactive", lambda *a, **k: True)
    app = engine.app_spec_from_deployment(
        SimpleNamespace(
            id=1,
            name="app1",
            project_id="p",
            app_config=None,
            created_at=datetime(2026, 1, 1),
        )
    )

    [rec] = [
        r
        for r in recommendations_for_apps([app])
        if r.rec_type == "inactivity"
    ]

    assert rec.patch is None
