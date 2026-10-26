import asyncio
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, TargetCloud
from app.routers import releases
from app.services import kube_client as kc
from app.services.clusters import state
from app.services.keycloak_service import get_current_user
from app.services.release import recorder
from app.services.release.source import KubeReleaseSource
from app.services.security import collector, store
from app.services.security.model import Source
from tests.test_kube_client_cloud import SECRET, SECRET_URL, _response
from tests.test_release_api import USER_TOKENS, keycloak  # noqa: F401
from tests.test_release_view import _rollout

ONPREM = "https://10.0.0.1:443"


@pytest.fixture(autouse=True)
def in_cluster(tmp_path, monkeypatch):
    (tmp_path / "token").write_text("sa-token\n")
    (tmp_path / "ca.crt").write_bytes(b"in-cluster")
    monkeypatch.setattr(kc, "SERVICE_ACCOUNT_DIR", tmp_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    monkeypatch.setattr(kc, "_credentials", {})


def _factory(cloud: TargetCloud):
    """Project "shop" on *cloud* (app 7 "web"), project "lab" on-prem (app 8)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for project, target in (("shop", cloud), ("lab", TargetCloud.ONPREM)):
            db.add(
                Project(
                    project_name=project,
                    owner_username="o",
                    target_cloud=target,
                )
            )
        for id_, project in ((7, "shop"), (8, "lab")):
            db.add(
                Deployment(
                    id=id_,
                    name="web",
                    template_id="k3s-gitops-app",
                    project_id=project,
                    provider_type=ProviderType.KUBERNETES,
                    status=DeploymentStatus.RUNNING,
                )
            )
        db.commit()
    return factory


def _client(factory):
    app = FastAPI()
    app.include_router(releases.router, prefix="/api")
    app.add_exception_handler(
        kc.ClusterNotConnectedError, state.not_connected_response
    )

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: USER_TOKENS["dev"]
    app.dependency_overrides[releases.get_release_source] = KubeReleaseSource
    return TestClient(app)


def _cluster(seen, rollouts=()):
    """Answer every request with an empty list, but the rollouts and the secret."""

    def request(method, url, **kwargs):
        seen.append((method, url))
        if url == SECRET_URL:
            return _response(200, SECRET)
        if url.endswith("/rollouts"):
            return _response(200, {"items": list(rollouts)})
        return _response(200, {"items": []})

    return request


def test_cloud_project_release_page_returns_not_connected(
    monkeypatch, keycloak  # noqa: F811
):
    monkeypatch.setattr(state, "_cache", {TargetCloud.AWS: "down"})
    client = _client(_factory(TargetCloud.AWS))

    with mock.patch.object(kc, "_session") as session:
        response = client.get("/api/deployments/7/release")

    assert (response.status_code, response.json()) == (
        503,
        {"detail": "cluster_not_connected", "cloud": "aws"},
    )
    session.request.assert_not_called()


def test_onprem_project_never_touches_cloud(
    monkeypatch, keycloak  # noqa: F811
):
    monkeypatch.setattr(state, "_cache", {TargetCloud.AWS: "down"})
    client = _client(_factory(TargetCloud.ONPREM))
    seen = []

    with mock.patch.object(kc, "_session") as session:
        session.request.side_effect = _cluster(seen)
        response = client.get("/api/deployments/7/release")

    assert response.status_code == 200
    assert seen and all(url.startswith(ONPREM) for _, url in seen)
    assert (mock.ANY, SECRET_URL) not in seen


def test_collectors_skip_down_clouds(monkeypatch):
    monkeypatch.setattr(state, "_cache", {TargetCloud.AWS: "down"})
    factory = _factory(TargetCloud.AWS)
    seen = []

    with (
        mock.patch.object(kc, "_session") as session,
        mock.patch.object(collector, "SessionLocal", factory),
        mock.patch.object(recorder, "SessionLocal", factory),
    ):
        session.request.side_effect = _cluster(seen)
        asyncio.run(collector.collect_once(cluster=True, github=False))
        recorder.record_all(KubeReleaseSource())

    urls = [url for _, url in seen]
    assert (
        f"{ONPREM}/apis/argoproj.io/v1alpha1/namespaces/lab-web/rollouts"
        in urls
    )
    assert not any("shop" in url for url in urls)
    with factory() as db:
        assert store.scan_state(db, "lab", None, Source.KYVERNO).status == "ok"
        assert store.scan_state(db, "shop", None, Source.KYVERNO).status != (
            "error"
        )


def test_cloud_write_routed(monkeypatch, keycloak):  # noqa: F811
    monkeypatch.setattr(state, "_cache", {TargetCloud.AWS: "up"})
    client = _client(_factory(TargetCloud.AWS))
    paused = _rollout(
        currentStepIndex=6,
        phase="Paused",
        pauseConditions=[
            {"reason": "CanaryPauseStep", "startTime": "2026-10-14T14:09:30Z"}
        ],
    )
    seen = []

    with mock.patch.object(kc, "_session") as session:
        session.request.side_effect = _cluster(seen, [paused])
        response = client.post(
            "/api/deployments/7/release/promote", json={"mode": "next"}
        )

    assert response.status_code == 200, response.text
    patches = [url for method, url in seen if method == "PATCH"]
    assert patches == [
        "https://x/apis/argoproj.io/v1alpha1/namespaces/release-lab"
        "/rollouts/web/status"
    ]
