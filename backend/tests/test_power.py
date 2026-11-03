import json
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.routers import power as router
from app.services import power
from app.services.environments import observe
from app.services.environments.observe import ComponentState, EnvironmentState
from app.services.kube_client import KubeUnavailableError
from tests.test_release_api import USER_TOKENS, keycloak  # noqa: F401

SCHEDULE = {"metadata": {"name": "shop-web-schedule"}}


def _state(pods):
    return EnvironmentState(
        namespace="shop-web",
        components={"app": ComponentState(pods=pods, ready=pods)},
    )


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(
            Deployment(
                id=7,
                name="web",
                template_id="k3s-gitops-app",
                project_id="shop",
                provider_type=ProviderType.KUBERNETES,
                status=DeploymentStatus.RUNNING,
                terraform_outputs=json.dumps({"app_type": "static"}),
            )
        )
        session.commit()
        yield session


@pytest.fixture
def client(db, keycloak):
    def build(who):
        app = FastAPI()
        app.include_router(router.router, prefix="/api")

        def override():
            yield db

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[router.get_current_user] = (
            lambda: USER_TOKENS[who]
        )
        return TestClient(app)

    return build


@pytest.fixture
def kube():
    with (
        mock.patch.object(power, "kube_list", return_value=[SCHEDULE]) as lst,
        mock.patch.object(power, "kube_get", return_value=None) as get,
        mock.patch.object(power, "kube_merge_patch", return_value={}) as patch,
        mock.patch.object(observe, "observe", return_value=_state(1)),
    ):
        yield mock.Mock(list=lst, get=get, patch=patch)


class TestSleepAndWake:
    def test_sleep_asks_offhours_guard_for_a_manual_sleep(self, client, kube):
        response = client("dev").post("/api/deployments/7/sleep")

        assert response.json() == {"schedules": 1}
        kube.patch.assert_called_once_with(
            "/apis/guard.3istor.com/v1alpha1/namespaces/shop-web"
            "/offhoursschedules/shop-web-schedule/status",
            {"status": {"currentState": "MANUAL_SLEEP"}},
            cloud=mock.ANY,
        )

    def test_wake_asks_offhours_guard_for_a_manual_wake(self, client, kube):
        client("dev").post("/api/deployments/7/wake")

        assert kube.patch.call_args.args[1] == {
            "status": {"currentState": "MANUAL_WAKE"}
        }

    def test_sleep_is_refused_when_the_app_has_no_schedule(self, client, kube):
        kube.list.return_value = []

        response = client("dev").post("/api/deployments/7/sleep")

        assert response.status_code == 409

    def test_a_guest_cannot_sleep_the_app(self, client, kube):
        response = client("guest").post("/api/deployments/7/sleep")

        assert response.status_code == 403
        kube.patch.assert_not_called()

    def test_an_outsider_cannot_wake_the_app(self, client, kube):
        assert (
            client("outsider").post("/api/deployments/7/wake").status_code
            == 403
        )

    def test_rbac_refusal_is_reported_as_403_with_the_cause(
        self, client, kube
    ):
        kube.patch.side_effect = KubeUnavailableError("PATCH /x: HTTP 403")

        response = client("dev").post("/api/deployments/7/sleep")

        assert response.status_code == 403
        assert "service account" in response.json()["detail"]

    def test_unreachable_cluster_is_reported_as_503(self, client, kube):
        kube.list.side_effect = KubeUnavailableError("down")

        assert (
            client("dev").post("/api/deployments/7/sleep").status_code == 503
        )


class TestRestart:
    def test_restart_stamps_the_pod_template_of_a_plain_deployment(
        self, client, kube
    ):
        response = client("dev").post("/api/deployments/7/restart")

        assert response.json() == {
            "restarted": ["shop-web-app-cnp-generic-app"]
        }
        path, body = kube.patch.call_args.args
        assert path == (
            "/apis/apps/v1/namespaces/shop-web/deployments/"
            "shop-web-app-cnp-generic-app"
        )
        annotations = body["spec"]["template"]["metadata"]["annotations"]
        assert "kubectl.kubernetes.io/restartedAt" in annotations

    def test_restart_sets_restart_at_on_an_argo_rollout(self, client, kube):
        kube.get.return_value = {"kind": "Rollout"}

        client("dev").post("/api/deployments/7/restart")

        path, body = kube.patch.call_args.args
        assert path.endswith("/rollouts/shop-web-app-cnp-generic-app")
        assert list(body["spec"]) == ["restartAt"]

    def test_restart_is_refused_while_the_app_is_asleep(self, client, kube):
        with mock.patch.object(observe, "observe", return_value=_state(0)):
            response = client("dev").post("/api/deployments/7/restart")

        assert response.status_code == 409
        kube.patch.assert_not_called()

    def test_a_guest_cannot_restart_the_app(self, client, kube):
        assert (
            client("guest").post("/api/deployments/7/restart").status_code
            == 403
        )


class TestPowerState:
    def test_a_guest_cannot_read_the_power_state(self, client, kube):
        assert (
            client("guest").get("/api/deployments/7/power").status_code == 403
        )

    def test_a_member_reads_the_power_state(self, client, kube):
        with mock.patch.object(observe, "observe", return_value=_state(0)):
            response = client("dev").get("/api/deployments/7/power")

        assert response.json() == {"asleep": True, "scheduled": True}
