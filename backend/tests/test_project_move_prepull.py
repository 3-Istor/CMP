from types import SimpleNamespace
from unittest import mock

import pytest

from app.models.project import TargetCloud
from app.services.moves import prepull

AWS, ONPREM = TargetCloud.AWS, TargetCloud.ONPREM
MOVE = SimpleNamespace(id=7, project="alpha", source="onprem", target="aws")
NS = "/api/v1/namespaces"
LABEL = "cnp.3istor.com/project"


def _node(name, ready="True", taints=None, unschedulable=False):
    return {
        "metadata": {"name": name},
        "spec": {"taints": taints or [], "unschedulable": unschedulable},
        "status": {"conditions": [{"type": "Ready", "status": ready}]},
    }


def _tpl(*images, init=(), secrets=()):
    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"image": i} for i in images],
                    "initContainers": [{"image": i} for i in init],
                    "imagePullSecrets": [{"name": s} for s in secrets],
                }
            }
        }
    }


class Kube:
    def __init__(self):
        self.items, self.created, self.deleted = {}, [], []

    def add(self, cloud, path, *objs):
        self.items.setdefault((cloud, path), []).extend(objs)

    def list(self, path, params=None, cloud=ONPREM):
        return self.items.get((cloud, path), [])

    def get(self, path, params=None, cloud=ONPREM):
        coll, name = path.rsplit("/", 1)
        return next(
            (
                o
                for o in self.items.get((cloud, coll), [])
                if o["metadata"]["name"] == name
            ),
            None,
        )

    def create(self, path, body, cloud=ONPREM):
        self.created.append((cloud, path, body))
        self.add(cloud, path, body)
        return body

    def delete(self, path, cloud=ONPREM):
        self.deleted.append((cloud, path))


@pytest.fixture
def kube():
    k = Kube()
    ns = {"metadata": {"name": "alpha-shop", "labels": {LABEL: "alpha"}}}
    k.add(ONPREM, NS, ns)
    k.add(AWS, NS, ns)
    k.add(
        ONPREM,
        "/apis/apps/v1/namespaces/alpha-shop/deployments",
        {
            "metadata": {"name": "a"},
            **_tpl(
                "img/a:1", "img/side:1", init=["img/init:1"], secrets=["reg"]
            ),
        },
        {"metadata": {"name": "b"}, **_tpl("img/a:1")},
    )
    # Rollout with workloadRef carries no template.
    k.add(
        ONPREM,
        "/apis/argoproj.io/v1alpha1/namespaces/alpha-shop/rollouts",
        {"metadata": {"name": "r"}, "spec": {"workloadRef": {"name": "a"}}},
    )
    k.add(
        ONPREM,
        "/apis/postgresql.cnpg.io/v1/namespaces/alpha-shop/clusters",
        {
            "metadata": {"name": "db"},
            "spec": {"imageName": "pg:16"},
            "status": {"image": "pg:16.2"},
        },
    )
    k.add(
        AWS,
        "/api/v1/nodes",
        _node("n1"),
        _node("n2"),
        _node("down", ready="False"),
        _node("tainted", taints=[{"effect": "NoSchedule"}]),
        _node("cordon", unschedulable=True),
    )
    with mock.patch.multiple(
        prepull,
        kube_list=k.list,
        kube_get=k.get,
        kube_create=k.create,
        kube_delete=k.delete,
    ), mock.patch("app.services.moves.steps.kube_list", k.list):
        yield k


def _pods(k):
    return [b for _, _, b in k.created]


def test_creates_one_pod_per_image_and_schedulable_node(kube):
    waits = prepull.pending(MOVE)
    pods = _pods(kube)
    # 4 distinct images (a, side, init, pg:16.2) x 2 nodes
    assert len(pods) == 8 and len(waits) == 8
    assert {p["spec"]["nodeName"] for p in pods} == {"n1", "n2"}
    assert {p["spec"]["containers"][0]["image"] for p in pods} == {
        "img/a:1",
        "img/side:1",
        "img/init:1",
        "pg:16.2",
    }
    a = next(
        p for p in pods if p["spec"]["containers"][0]["image"] == "img/a:1"
    )
    assert a["spec"]["imagePullSecrets"] == [{"name": "reg"}]
    assert a["metadata"]["labels"] == {
        "cnp.3istor.com/prepull": "7",
        LABEL: "alpha",
    }
    assert a["metadata"]["name"].startswith("prepull-7-")


def test_idempotent_and_state_reading(kube):
    prepull.pending(MOVE)
    kube.created.clear()
    assert len(prepull.pending(MOVE)) == 8  # all pods without status: pulling
    assert kube.created == []
    for pod in kube.items[(AWS, f"{NS}/alpha-shop/pods")]:
        image = pod["spec"]["containers"][0]["image"]
        reason = {
            "img/a:1": "ErrImagePull",
            "img/side:1": "ContainerCreating",
        }.get(image)
        state = (
            {"waiting": {"reason": reason}}
            if reason
            else {"terminated": {"exitCode": 0}}
        )
        pod["status"] = {"containerStatuses": [{"state": state}]}
    waits = prepull.pending(MOVE)
    # failed (a) and pulled (init, pg) are not waited for; side is.
    assert sorted(waits) == [
        "Pulling img/side:1 on n1",
        "Pulling img/side:1 on n2",
    ]


def test_waits_for_target_namespace(kube):
    kube.items[(AWS, NS)] = []
    assert prepull.pending(MOVE) == ["Waiting for namespace alpha-shop on aws"]
    assert kube.created == []


def test_cleanup_deletes_only_this_moves_pods(kube):
    prepull.pending(MOVE)
    kube.items[(AWS, f"{NS}/alpha-shop/pods")].append(
        {
            "metadata": {
                "name": "other",
                "labels": {"cnp.3istor.com/prepull": "8"},
            }
        }
    )
    prepull.cleanup(MOVE)
    assert len(kube.deleted) == 8 and all(
        "other" not in p for _, p in kube.deleted
    )


def test_refused_pod_is_a_wait_not_an_error(kube):
    def refuse(path, body, cloud=ONPREM):
        raise prepull.KubeUnavailableError("POST: HTTP 403", 403)

    with mock.patch.object(prepull, "kube_create", refuse):
        assert len(prepull.pending(MOVE)) == 8
