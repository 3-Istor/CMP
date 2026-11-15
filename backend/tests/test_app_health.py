import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx
import pytest

from app.models.deployment import Deployment, ProviderType
from app.routers import infra
from app.services import app_health, monitoring_service
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
NS = "shop-web"
STATIC = "shop-web-app-cnp-generic-app"


def _ts(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _pod(name, ready=True, minutes_ago=60, crashloop=False):
    status = {
        "conditions": [
            {
                "type": "Ready",
                "status": "True" if ready else "False",
                "lastTransitionTime": _ts(minutes_ago),
            }
        ],
        "containerStatuses": [],
    }
    if crashloop:
        status["containerStatuses"] = [
            {"state": {"waiting": {"reason": "CrashLoopBackOff"}}}
        ]
    return {"metadata": {"name": name}, "status": status}


def _rs(
    desired, key="deployment.kubernetes.io/desired-replicas", replicas=None
):
    return {
        "metadata": {
            "annotations": {key: str(desired)},
            "creationTimestamp": _ts(60),
        },
        "spec": {"replicas": desired if replicas is None else replicas},
    }


def _schedule(target, state):
    return {
        "spec": {"targetRefs": [{"name": target}]},
        "status": {"currentState": state},
    }


def _deployment(app_type="static", provider=ProviderType.KUBERNETES):
    return Deployment(
        id=1,
        name="web",
        template_id="k3s",
        project_id="shop",
        provider_type=provider,
        app_config=json.dumps({"target_cloud": "onprem"}),
        terraform_outputs=json.dumps({"app_type": app_type}),
    )


def _cluster(pods=(), rs=(), schedules=(), apps=()):
    """kube_list answers keyed on the API path; per-component for fullstack."""

    def kube_list(path, params=None, cloud=None):
        selector = (params or {}).get("labelSelector", "")
        if path.endswith("/pods"):
            return [p for p in pods if _wanted(p, selector)]
        if path.endswith("/replicasets"):
            return [r for r in rs if _wanted(r, selector)]
        if path.endswith("/offhoursschedules"):
            return list(schedules)
        return list(apps)

    return kube_list


def _wanted(item, selector):
    owner = item.get("component")
    return owner is None or selector.endswith(f"-{owner}")


def _vm(restarts):
    def get(url, params=None, timeout=None):
        if restarts is None:
            raise httpx.ConnectError("down")
        body = {
            "data": {
                "result": [
                    {"metric": {"pod": pod}, "value": [0, str(count)]}
                    for pod, count in restarts.items()
                ]
            }
        }
        return mock.Mock(raise_for_status=lambda: None, json=lambda: body)

    return get


def _health(deployment, kube_list, restarts=()):
    with mock.patch.object(
        app_health, "kube_list", kube_list
    ), mock.patch.object(
        app_health.httpx, "get", _vm({} if restarts == () else restarts)
    ):
        return app_health._read_app_health(deployment, NOW)


def _only(health):
    assert len(health.components) == 1
    return health.components[0]


def test_healthy_when_every_replica_is_ready():
    health = _health(
        _deployment(),
        _cluster(pods=[_pod("p1"), _pod("p2")], rs=[_rs(2)]),
    )

    assert health.status == "healthy"
    assert (_only(health).ready, _only(health).desired) == (2, 2)


def test_down_when_no_replica_is_ready():
    health = _health(
        _deployment(), _cluster(pods=[_pod("p1", ready=False)], rs=[_rs(1)])
    )

    assert health.status == "down"
    assert _only(health).reason == "no_ready_replica"


def test_down_reports_crashloop_when_nothing_is_ready():
    health = _health(
        _deployment(),
        _cluster(pods=[_pod("p1", ready=False, crashloop=True)], rs=[_rs(1)]),
    )

    assert _only(health).reason == "crashloop"


def test_down_only_after_five_minutes_without_a_ready_replica():
    pods = [_pod("p1", ready=False, minutes_ago=4)]
    rs = [_rs(1)]

    early = app_health.classify(pods, rs, False, None, NOW)[:2]
    later = app_health.classify(
        pods, rs, False, None, NOW + timedelta(minutes=2)
    )[:2]

    assert (early, later) == (
        ("degraded", "no_ready_replica"),
        ("down", "no_ready_replica"),
    )


def test_no_pod_at_all_is_down_at_once():
    rs = _rs(1)
    rs["metadata"]["creationTimestamp"] = _ts(1)

    assert app_health.classify([], [rs], False, None, NOW)[0] == "down"


def test_degraded_when_a_pod_crashloops_while_another_serves():
    health = _health(
        _deployment(),
        _cluster(
            pods=[_pod("p1"), _pod("p2", ready=False, crashloop=True)],
            rs=[_rs(2)],
        ),
    )

    assert health.status == "degraded"
    assert _only(health).reason == "crashloop"


def test_ready_below_desired_for_under_five_minutes_is_healthy():
    health = _health(
        _deployment(),
        _cluster(
            pods=[_pod("p1"), _pod("p2", ready=False, minutes_ago=4)],
            rs=[_rs(2)],
        ),
    )

    assert health.status == "healthy"


def test_ready_below_desired_for_over_five_minutes_is_degraded():
    health = _health(
        _deployment(),
        _cluster(
            pods=[_pod("p1"), _pod("p2", ready=False, minutes_ago=6)],
            rs=[_rs(2)],
        ),
    )

    assert health.status == "degraded"
    assert _only(health).reason == "rollout_slow"


def test_five_minute_rule_follows_the_clock():
    pods = [_pod("p1"), _pod("p2", ready=False, minutes_ago=4)]
    rs = [_rs(2)]

    early = app_health.classify(pods, rs, False, None, NOW)[0]
    later = app_health.classify(
        pods, rs, False, None, NOW + timedelta(minutes=2)
    )[0]

    assert (early, later) == ("healthy", "degraded")


def test_degraded_with_three_restarts_in_fifteen_minutes():
    health = _health(
        _deployment(),
        _cluster(pods=[_pod("p1")], rs=[_rs(1)]),
        restarts={"p1": 3, "other": 9},
    )

    assert health.status == "degraded"
    assert (_only(health).reason, _only(health).restarts_15m) == (
        "restarts",
        3,
    )


def test_two_restarts_stay_healthy():
    health = _health(
        _deployment(),
        _cluster(pods=[_pod("p1")], rs=[_rs(1)]),
        restarts={"p1": 2},
    )

    assert health.status == "healthy"


def test_unreadable_metrics_do_not_degrade_the_app():
    health = _health(
        _deployment(), _cluster(pods=[_pod("p1")], rs=[_rs(1)]), restarts=None
    )

    assert (health.status, _only(health).restarts_15m) == ("healthy", None)


def test_asleep_when_the_schedule_is_sleeping():
    for state in ("SLEEPING", "MANUAL_SLEEP"):
        health = _health(
            _deployment(),
            _cluster(rs=[_rs(0)], schedules=[_schedule(STATIC, state)]),
        )

        assert health.status == "asleep"


def test_asleep_wins_over_down():
    health = _health(
        _deployment(),
        _cluster(
            pods=[_pod("p1", ready=False)],
            rs=[_rs(1)],
            schedules=[_schedule(STATIC, "SLEEPING")],
        ),
    )

    assert _only(health).state == "asleep"


def test_awake_schedule_does_not_hide_an_outage():
    health = _health(
        _deployment(),
        _cluster(
            pods=[_pod("p1", ready=False)],
            rs=[_rs(1)],
            schedules=[_schedule(STATIC, "AWAKE")],
        ),
    )

    assert health.status == "down"


def test_argo_rollouts_replicasets_give_the_desired_count():
    health = _health(
        _deployment(),
        _cluster(
            pods=[_pod("p1")],
            rs=[_rs(2, "rollout.argoproj.io/desired-replicas"), _rs(0)],
        ),
    )

    assert (_only(health).ready, _only(health).desired) == (1, 2)


def test_scaled_down_old_replicasets_are_ignored():
    assert app_health.desired_replicas([_rs(3, replicas=0), _rs(1)]) == 1


def test_unknown_without_any_workload():
    health = _health(_deployment(), _cluster())

    assert (health.status, _only(health).reason) == ("unknown", "no_workload")


def test_kubernetes_api_error_means_not_connected():
    def broken(*_args, **_kwargs):
        raise KubeUnavailableError("timeout")

    health = _health(_deployment(), broken)

    assert health.status == "not_connected"
    assert _only(health).reason == "cluster_unreachable"


def test_public_cloud_down_means_not_connected():
    def down(*_args, **_kwargs):
        raise ClusterNotConnectedError(app_health.TargetCloud.AWS)

    health = _health(_deployment(), down)

    assert health.status == "not_connected"


def test_argocd_status_is_reported_per_component():
    apps = [
        {
            "metadata": {"name": "shop-web-app"},
            "status": {
                "sync": {"status": "Synced"},
                "health": {"status": "Healthy"},
            },
        }
    ]
    health = _health(
        _deployment(),
        _cluster(pods=[_pod("p1")], rs=[_rs(1)], apps=apps),
    )

    assert (_only(health).argocd_sync, _only(health).argocd_health) == (
        "Synced",
        "Healthy",
    )


def _fullstack(frontend_ok=True, backend_ok=True, backend_asleep=False):
    pods = [
        {**_pod("fe", ready=frontend_ok), "component": "frontend"},
        {**_pod("be", ready=backend_ok), "component": "backend"},
    ]
    rs = [
        {**_rs(1), "component": "frontend"},
        {**_rs(1), "component": "backend"},
    ]
    schedules = (
        [_schedule("shop-web-backend-cnp-generic-app", "SLEEPING")]
        if backend_asleep
        else []
    )
    return _cluster(pods=pods, rs=rs, schedules=schedules)


def test_fullstack_app_has_one_row_per_component():
    health = _health(_deployment("fullstack"), _fullstack())

    assert [c.name for c in health.components] == ["frontend", "backend"]


def test_app_takes_the_worst_state_of_its_components():
    health = _health(_deployment("fullstack"), _fullstack(backend_ok=False))

    states = {c.name: c.state for c in health.components}
    assert (states, health.status) == (
        {"frontend": "healthy", "backend": "down"},
        "down",
    )


def test_asleep_component_does_not_hide_a_healthy_one():
    health = _health(_deployment("fullstack"), _fullstack(backend_asleep=True))

    assert health.status == "asleep"


@pytest.mark.parametrize(
    "states,expected",
    [
        (["healthy", "degraded"], "degraded"),
        (["degraded", "down"], "down"),
        (["healthy", "asleep"], "asleep"),
        (["healthy", "not_connected"], "not_connected"),
        (["healthy"], "healthy"),
        ([], "unknown"),
    ],
)
def test_worst_orders_states_by_severity(states, expected):
    assert app_health.worst(states) == expected


def test_vm_deployments_keep_the_legacy_health_path():
    legacy = monitoring_service.AppHealthResponse(
        deployment_name="web",
        status="healthy",
        aws_frontend=None,
        openstack_backend=None,
    )
    db = mock.Mock()
    deployment = _deployment(provider=ProviderType.LEGACY_HYBRID)

    with mock.patch.object(
        infra, "_get_app_or_404", return_value=deployment
    ), mock.patch.object(
        monitoring_service,
        "get_app_health",
        mock.AsyncMock(return_value=legacy),
    ) as vm, mock.patch.object(
        infra, "get_kubernetes_app_health", mock.AsyncMock()
    ) as k8s:
        result = asyncio.run(infra.get_deployment_health(1, {}, db))

    vm.assert_awaited_once_with("web")
    k8s.assert_not_awaited()
    assert result.components == []


from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import get_db  # noqa: E402
from app.services.keycloak_service import get_current_user  # noqa: E402
from tests.test_release_api import (  # noqa: E402,F401
    USER_TOKENS,
    keycloak,
    session_factory,
)


def _infra_client(session_factory, who):
    app = FastAPI()
    app.include_router(infra.router, prefix="/api")

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: USER_TOKENS[who]
    return TestClient(app)


def _healthy(deployment):
    return monitoring_service.AppHealthResponse(
        deployment_name=deployment.name,
        status="healthy",
        aws_frontend=None,
        openstack_backend=None,
    )


def test_an_outsider_cannot_read_the_health_of_another_projects_app(
    session_factory, keycloak
):
    # Arrange
    client = _infra_client(session_factory, "outsider")

    # Act
    with mock.patch.object(
        infra, "get_kubernetes_app_health", mock.AsyncMock()
    ) as k8s:
        response = client.get("/api/infra/deployments/7/health")

    # Assert
    assert (response.status_code, k8s.await_count) == (403, 0)


def test_a_project_guest_reads_the_health_of_its_app(
    session_factory, keycloak
):
    # Arrange
    client = _infra_client(session_factory, "guest")

    # Act
    with mock.patch.object(
        infra,
        "get_kubernetes_app_health",
        mock.AsyncMock(side_effect=_healthy),
    ):
        response = client.get("/api/infra/deployments/7/health")

    # Assert
    assert (response.status_code, response.json()["status"]) == (
        200,
        "healthy",
    )
