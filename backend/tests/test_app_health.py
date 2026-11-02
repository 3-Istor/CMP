from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.deployment import Deployment, ProviderType
from app.models.project import Project, TargetCloud
from app.routers import infra
from app.services import kube_health, monitoring_service
from app.services.keycloak_service import get_current_user
from app.services.kube_client import ClusterNotConnectedError

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
PREFIX = "test-hugo-app-test-"


def _ago(minutes):
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pod(name, ready=True, since=60, waiting=None):
    state = {"waiting": {"reason": waiting}} if waiting else {"running": {}}
    return {
        "metadata": {"name": name, "creationTimestamp": _ago(120)},
        "status": {
            "phase": "Running",
            "conditions": [
                {
                    "type": "Ready",
                    "status": str(ready),
                    "lastTransitionTime": _ago(since),
                }
            ],
            "containerStatuses": [
                {"name": "cnp-generic-app", "ready": ready, "state": state}
            ],
        },
    }


def _rs(desired, replicas=None):
    return {
        "metadata": {
            "annotations": {
                "deployment.kubernetes.io/desired-replicas": str(desired)
            }
        },
        "spec": {"replicas": desired if replicas is None else replicas},
    }


def _sleeping(component, state="SLEEPING"):
    return {
        "spec": {
            "targetRefs": [{"name": f"{PREFIX}{component}-cnp-generic-app"}]
        },
        "status": {"currentState": state},
    }


HEALTHY = {
    "pods": {"frontend": [_pod("fe-1")], "backend": [_pod("be-1")]},
    "replica_sets": {"frontend": [_rs(1)], "backend": [_rs(1)]},
}


def _app():
    return Deployment(
        id=32,
        name="app-test",
        template_id="k3s-gitops-app",
        project_id="test-hugo",
        provider_type=ProviderType.KUBERNETES,
        terraform_outputs='{"app_type": "fullstack"}',
    )


@pytest.fixture
def restarts():
    """Restarts in 15 min per pod, as VictoriaMetrics answers them."""
    counts: dict[str, float] = {}
    response = mock.Mock()
    response.json.side_effect = lambda: {
        "data": {
            "result": [
                {"metric": {"pod": pod}, "value": [0, str(n)]}
                for pod, n in counts.items()
            ]
        }
    }
    with mock.patch.object(kube_health.httpx, "get", return_value=response):
        yield counts


def _health(cluster, cloud=TargetCloud.ONPREM, argocd=None):
    def kube_list(path, params=None, cloud=TargetCloud.ONPREM):
        if path.endswith("/offhoursschedules"):
            return cluster.get("schedules", [])
        kind = "pods" if path.endswith("/pods") else "replica_sets"
        release = params["labelSelector"].split("=", 1)[1]
        return cluster[kind].get(release.removeprefix(PREFIX), [])

    application = argocd or {
        "status": {
            "sync": {"status": "Synced"},
            "health": {"status": "Healthy"},
        }
    }
    with mock.patch.object(
        kube_health, "kube_list", side_effect=kube_list
    ), mock.patch.object(kube_health, "kube_get", return_value=application):
        return kube_health.app_health(_app(), cloud, now=NOW)


def _status(health):
    return health.status, {c.name: c.status for c in health.components}


def test_a_fullstack_app_shows_both_components_with_their_replicas(restarts):
    health = _health(HEALTHY)

    assert health.status == "healthy"
    assert [c.model_dump() for c in health.components] == [
        {
            "name": "frontend",
            "status": "healthy",
            "ready_replicas": 1,
            "desired_replicas": 1,
            "restarts_15m": 0,
            "argocd": {"sync": "Synced", "health": "Healthy"},
        },
        {
            "name": "backend",
            "status": "healthy",
            "ready_replicas": 1,
            "desired_replicas": 1,
            "restarts_15m": 0,
            "argocd": {"sync": "Synced", "health": "Healthy"},
        },
    ]


def test_a_pod_in_crash_loop_makes_the_app_degraded(restarts):
    cluster = {
        **HEALTHY,
        "pods": {
            "frontend": [_pod("fe-1")],
            "backend": [
                _pod("be-1"),
                _pod("be-2", ready=False, since=1, waiting="CrashLoopBackOff"),
            ],
        },
    }

    assert _status(_health(cluster)) == (
        "degraded",
        {"frontend": "healthy", "backend": "degraded"},
    )


def test_an_app_in_offhours_is_asleep_not_down(restarts):
    cluster = {
        "pods": {},
        "replica_sets": {"frontend": [_rs(0, 0)], "backend": [_rs(0, 0)]},
        "schedules": [_sleeping("frontend"), _sleeping("backend")],
    }

    assert _status(_health(cluster)) == (
        "asleep",
        {"frontend": "asleep", "backend": "asleep"},
    )


def test_a_manual_sleep_is_asleep_too(restarts):
    cluster = {
        **HEALTHY,
        "pods": {"frontend": [], "backend": [_pod("be-1")]},
        "schedules": [_sleeping("frontend", "MANUAL_SLEEP")],
    }

    assert _status(_health(cluster)) == (
        "asleep",
        {"frontend": "asleep", "backend": "healthy"},
    )


def test_no_ready_replica_outside_offhours_is_down(restarts):
    cluster = {
        **HEALTHY,
        "pods": {
            "frontend": [_pod("fe-1", ready=False, since=1)],
            "backend": [_pod("be-1")],
        },
        "schedules": [_sleeping("frontend", "AWAKE")],
    }

    assert _status(_health(cluster)) == (
        "down",
        {"frontend": "down", "backend": "healthy"},
    )


@pytest.mark.parametrize("minutes, status", [(2, "healthy"), (6, "degraded")])
def test_missing_replicas_degrade_only_after_five_minutes(
    restarts, minutes, status
):
    cluster = {
        "pods": {
            "frontend": [
                _pod("fe-1"),
                _pod("fe-2", ready=False, since=minutes),
            ],
            "backend": [_pod("be-1")],
        },
        "replica_sets": {"frontend": [_rs(2)], "backend": [_rs(1)]},
    }

    health = _health(cluster)

    assert health.status == status
    assert (
        health.components[0].ready_replicas,
        health.components[0].desired_replicas,
    ) == (1, 2)


def test_three_restarts_in_fifteen_minutes_degrade(restarts):
    restarts.update({"be-1": 2.9, "fe-1": 2})

    health = _health(HEALTHY)

    assert _status(health) == (
        "degraded",
        {"frontend": "healthy", "backend": "degraded"},
    )
    assert [c.restarts_15m for c in health.components] == [2, 3]


def test_restarts_are_unknown_when_metrics_cannot_be_read():
    with mock.patch.object(
        kube_health.httpx,
        "get",
        side_effect=kube_health.httpx.ConnectError("x"),
    ):
        health = _health(HEALTHY)

    assert health.status == "healthy"
    assert [c.restarts_15m for c in health.components] == [None, None]


def test_a_cloud_app_whose_cluster_is_down_is_not_connected(restarts):
    def kube_list(*_args, **_kwargs):
        raise ClusterNotConnectedError(TargetCloud.AWS)

    argocd = {
        "status": {
            "sync": {"status": "Unknown"},
            "health": {"status": "Healthy"},
        }
    }
    with mock.patch.object(
        kube_health, "kube_list", side_effect=kube_list
    ), mock.patch.object(kube_health, "kube_get", return_value=argocd):
        health = kube_health.app_health(_app(), TargetCloud.AWS, now=NOW)

    assert _status(health) == (
        "not_connected",
        {"frontend": "not_connected", "backend": "not_connected"},
    )
    assert health.components[0].ready_replicas is None
    assert health.components[0].argocd.sync == "Unknown"


def _client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(
            Project(
                project_name="test-hugo",
                owner_username="o",
                target_cloud=TargetCloud.GCP,
            )
        )
        db.add(_app())
        db.add(
            Deployment(
                id=33,
                name="legacy",
                template_id="hybrid",
                provider_type=ProviderType.LEGACY_HYBRID,
            )
        )
        db.commit()

    def override_db():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(infra.router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {}
    return TestClient(app)


def test_the_health_route_reads_kubernetes_apps_on_their_project_cluster():
    answer = monitoring_service.AppHealthResponse(
        deployment_name="app-test", status="healthy"
    )
    with mock.patch.object(
        kube_health, "app_health", return_value=answer
    ) as kube, mock.patch.object(
        monitoring_service, "get_app_health"
    ) as legacy:
        response = _client().get("/api/infra/deployments/32/health")

    assert response.json()["status"] == "healthy"
    assert kube.call_args.args[1] == TargetCloud.GCP
    legacy.assert_not_called()


def test_the_health_route_keeps_reading_vm_apps_from_their_clouds():
    answer = monitoring_service.AppHealthResponse(
        deployment_name="legacy", status="degraded"
    )
    with mock.patch.object(
        monitoring_service, "get_app_health", return_value=answer
    ) as legacy, mock.patch.object(kube_health, "app_health") as kube:
        response = _client().get("/api/infra/deployments/33/health")

    assert response.json()["status"] == "degraded"
    legacy.assert_called_once_with("legacy")
    kube.assert_not_called()
