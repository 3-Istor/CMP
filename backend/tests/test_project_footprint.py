from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.project import TargetCloud
from app.models.project_footprint import ProjectFootprint
from app.services.kube_client import KubeUnavailableError
from app.services.moves import footprint

AWS = TargetCloud.AWS
GIB = 2**30


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _ns(name, project):
    return {
        "metadata": {
            "name": name,
            "labels": {"cnp.3istor.com/project": project},
        }
    }


def _pod(ns, cpu, memory, phase="Running"):
    return {
        "metadata": {"namespace": ns},
        "status": {"phase": phase},
        "spec": {
            "containers": [
                {"resources": {"requests": {"cpu": cpu, "memory": memory}}}
            ]
        },
    }


def _cluster(namespaces, pods):
    def kube_list(path, params=None, cloud=None):
        return namespaces if path == "/api/v1/namespaces" else pods

    return mock.patch.object(footprint, "kube_list", kube_list)


def _rows(db):
    return {
        (r.project, r.app): (r.cpu, r.memory_bytes, r.pods)
        for r in db.query(ProjectFootprint)
    }


def test_apps_come_from_namespaces_staging_merges_into_prod(db):
    namespaces = [
        _ns(n, "alpha")
        for n in ("alpha-system", "alpha-shop", "alpha-shop-stg")
    ]
    pods = [
        _pod("alpha-system", "100m", "1Gi"),
        _pod("alpha-shop", "500m", "1Gi"),
        _pod("alpha-shop-stg", "100m", "1Gi"),
        _pod("alpha-shop", "1", "1Gi", phase="Succeeded"),
        _pod("kube-system", "1", "1Gi"),
    ]
    with _cluster(namespaces, pods):
        footprint.refresh(db, AWS)

    assert _rows(db) == {
        ("alpha", "system"): (0.1, GIB, 1),
        ("alpha", "shop"): (0.6, 2 * GIB, 2),
    }


def test_refresh_overwrites_and_an_unreachable_cluster_erases_nothing(db):
    with _cluster(
        [_ns("alpha-shop", "alpha")], [_pod("alpha-shop", "1", "1Gi")]
    ):
        footprint.refresh(db, AWS)
    with _cluster(
        [_ns("alpha-shop", "alpha")], [_pod("alpha-shop", "2", "1Gi")]
    ):
        footprint.refresh(db, AWS)
    assert _rows(db) == {("alpha", "shop"): (2.0, GIB, 1)}

    def down(*_a, **_k):
        raise KubeUnavailableError("down")

    with mock.patch.object(footprint, "kube_list", down):
        with pytest.raises(KubeUnavailableError):
            footprint.refresh(db, AWS)
    assert _rows(db) == {("alpha", "shop"): (2.0, GIB, 1)}


def test_a_project_gone_from_a_reachable_cluster_is_dropped(db):
    with _cluster(
        [_ns("alpha-shop", "alpha")], [_pod("alpha-shop", "1", "1Gi")]
    ):
        footprint.refresh(db, AWS)
    with _cluster([], []):
        footprint.refresh(db, AWS)
    assert _rows(db) == {}
