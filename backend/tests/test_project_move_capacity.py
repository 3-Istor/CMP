import asyncio
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, TargetCloud
from app.services.kube_client import KubeUnavailableError
from app.services.moves import capacity, steps
from app.services.security import restore_commit

GCP = TargetCloud.GCP
AWS = TargetCloud.AWS


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
            Project(project_name="alpha", owner_username="u", target_cloud=AWS)
        )
        session.commit()
        yield session


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


def test_check_reports_need_free_and_fit():
    with _cluster([_node("2", "4Gi")], [_pod("1200m", "1Gi")]):
        check = capacity.check((1.2, 2.5 * GIB), GCP, estimated=False)

    assert check == {
        "target": "gcp",
        "need_cpu": 1.2,
        "need_memory_bytes": 2.5 * GIB,
        "free_cpu": 0.8,
        "free_memory_bytes": 3 * GIB,
        "estimated": False,
        "fits": False,
    }
    assert capacity.message(check) == (
        "Not enough room on GCP: needs 1.2 CPU / 2.5 GiB, free 0.8 / 3.0"
    )


def test_enough_room_or_an_unknown_target_fits():
    with _cluster([_node("4", "8Gi")], []):
        assert capacity.check((1, GIB), GCP, False)["fits"] is True

    def forbidden(*_a, **_k):
        raise KubeUnavailableError("nodes", 403)

    with mock.patch.object(capacity, "kube_list", forbidden):
        unknown = capacity.check((99, 99 * GIB), GCP, False)

    assert unknown["fits"] is True and unknown["free_cpu"] is None


def test_an_app_is_its_components_requests_times_replicas():
    # Chart defaults: 100m / 128Mi, one replica.
    assert capacity.component_need({}) == (0.1, 128 * 2**20)
    values = {
        "replicaCount": 2,
        "resources": {"requests": {"cpu": "250m", "memory": "256Mi"}},
    }
    assert capacity.component_need(values) == (0.5, 512 * 2**20)
    # Autoscaled: the floor.
    scaled = {"autoscaling": {"enabled": True, "minReplicas": 3}}
    assert capacity.component_need(scaled) == (
        pytest.approx(0.3),
        3 * 128 * 2**20,
    )


def test_an_app_without_requests_takes_the_limit_range_default():
    assert capacity.component_need({"resources": None}) == (
        0.05,
        128 * 2**20,
    )


def test_a_database_takes_the_limit_range_per_container_and_a_pooler():
    values = {
        "db": {
            "enabled": True,
            "instances": 2,
            "backup": {"enabled": True},
            "pooler": {"enabled": True, "instances": 2},
        }
    }
    cpu, memory = capacity.component_need(values)

    # app 0.1 + 2 instances x 2 containers x 0.05 + 2 poolers x 0.01
    assert round(cpu, 3) == 0.32
    assert memory == (128 + 4 * 128 + 2 * 64) * 2**20


def test_a_two_app_project_estimate_with_its_system_part():
    backend = {"db": {"enabled": True, "backup": {"enabled": True}}}
    fullstack = [{}, backend]  # frontend, backend + CNPG postgres + barman
    static = [{}]

    cpu, memory = capacity.estimate([fullstack, static])

    # 3 components x 100m/128Mi + 2 CNPG containers x 50m/128Mi + system.
    assert round(cpu, 3) == round(0.4 + capacity.SYSTEM_CPU, 3)
    assert memory == 5 * 128 * 2**20 + capacity.SYSTEM_MEMORY


def test_an_estimate_over_the_room_warns_and_a_real_need_refuses():
    with _cluster([_node("1", "2Gi")], []):
        estimated = capacity.check((2, GIB), GCP, estimated=True)
        real = capacity.check((2, GIB), GCP, estimated=False)

    assert estimated["fits"] is False and estimated["estimated"] is True
    assert "estimated" in capacity.message(estimated)
    assert capacity.refusal(real) and capacity.warning(estimated)
    assert capacity.refusal(estimated) is None
    assert capacity.warning(real) is None


def test_a_lost_source_is_estimated_from_the_values_and_only_warns(db):
    db.add(
        Deployment(
            name="shop",
            template_id="k3s-gitops-app",
            provider_type=ProviderType.KUBERNETES,
            project_id="alpha",
            status=DeploymentStatus.RUNNING,
        )
    )
    db.commit()

    def lost(*_a, **_k):
        raise KubeUnavailableError("down")

    read = mock.AsyncMock(return_value=[{}, {"db": {"enabled": True}}])
    with (
        mock.patch.object(steps, "_app_namespaces", lost),
        mock.patch.object(restore_commit, "read_components", read),
        _cluster([_node("1", "2Gi")], [_pod("900m", "1Gi")]),
    ):
        result = asyncio.run(steps.capacity_check(db, "alpha", AWS, GCP))
        full, warning = asyncio.run(steps._no_room(db, "alpha", AWS, GCP))

    assert result["estimated"] is True and result["fits"] is False
    assert round(result["need_cpu"], 3) == round(0.25 + capacity.SYSTEM_CPU, 3)
    assert full is None and "estimated" in warning
