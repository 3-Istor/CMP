from unittest import mock

from app.models.project import TargetCloud
from app.services.kube_client import KubeUnavailableError
from app.services.moves import capacity, steps

GCP = TargetCloud.GCP
GIB = capacity.GIB


def _pod(cpu, memory, phase="Running"):
    return {
        "status": {"phase": phase},
        "spec": {
            "containers": [
                {"resources": {"requests": {"cpu": cpu, "memory": memory}}}
            ]
        },
    }


def _node(cpu, memory):
    return {"status": {"allocatable": {"cpu": cpu, "memory": memory}}}


def _cluster(nodes, pods):
    def kube_list(path, params=None, cloud=None):
        return nodes if path == "/api/v1/nodes" else pods

    return mock.patch.object(capacity, "kube_list", kube_list)


def test_free_is_allocatable_minus_live_requests():
    pods = [_pod("500m", "1Gi"), _pod("2", "4Gi", phase="Succeeded")]

    with _cluster([_node("4", "8Gi"), _node("4", "8Gi")], pods):
        assert capacity.free(GCP) == (7.5, 15 * GIB)


def test_the_refusal_names_the_numbers():
    with _cluster([_node("2", "4Gi")], [_pod("1200m", "1Gi")]):
        message = capacity.refusal((1.2, 2.5 * GIB), GCP)

    assert message == (
        "Not enough room on GCP: needs 1.2 CPU / 2.5 GiB, free 0.8 / 3.0"
    )


def test_enough_room_or_an_unknown_target_does_not_refuse():
    with _cluster([_node("4", "8Gi")], []):
        assert capacity.refusal((1, GIB), GCP) is None

    def forbidden(*_a, **_k):
        raise KubeUnavailableError("nodes", 403)

    with mock.patch.object(capacity, "kube_list", forbidden):
        assert capacity.refusal((99, 99 * GIB), GCP) is None


def test_a_lost_source_is_sized_by_the_namespace_quotas():
    apps = [
        {"spec": {"destination": {"namespace": ns}}}
        for ns in ("alpha-system", "alpha-shop", "alpha-shop", "alpha-blog")
    ]

    def lost(*_a, **_k):
        raise KubeUnavailableError("down")

    with (
        mock.patch.object(steps, "_app_namespaces", lost),
        mock.patch.object(steps, "_project_apps", return_value=apps),
    ):
        assert steps._needed("alpha", TargetCloud.AWS) == (2.0, 4 * GIB)
