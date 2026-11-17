import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.services import health_poller

OUTPUTS = json.dumps({"app_url": "https://web-shop.3istor.com"})


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with mock.patch.object(health_poller, "SessionLocal", session_factory):
        yield session_factory


@pytest.fixture
def notify():
    with mock.patch.object(
        health_poller.notifications, "notify_project"
    ) as notify_project:
        yield notify_project


def add(factory, name="web", **fields):
    row = {
        "name": name,
        "template_id": "k3s-gitops-app",
        "project_id": "shop",
        "provider_type": ProviderType.KUBERNETES,
        "status": DeploymentStatus.FAILED,
        "step_message": "Deployment interrupted by a CMP restart.",
        "terraform_outputs": OUTPUTS,
        **fields,
    }
    with factory() as db:
        db.add(Deployment(**row))
        db.commit()


def read(factory, name="web"):
    with factory() as db:
        return db.query(Deployment).filter_by(name=name).one()


def health(*statuses):
    return mock.AsyncMock(
        side_effect=[
            s if isinstance(s, Exception) else SimpleNamespace(status=s)
            for s in statuses
        ]
    )


def recover(reader):
    with mock.patch.object(health_poller, "get_kubernetes_app_health", reader):
        asyncio.run(health_poller.recover_failed_apps())


def test_a_failed_app_that_is_healthy_goes_back_to_running(factory, notify):
    add(factory)

    recover(health("healthy"))

    row = read(factory)
    assert row.status == DeploymentStatus.RUNNING
    assert row.step_message == "Running - recovered after a failed deployment"
    notify.assert_called_once()
    assert notify.call_args.kwargs["kind"] == "alert"
    assert notify.call_args.kwargs["title_key"] == "app_recovered"


@pytest.mark.parametrize(
    "status", ["degraded", "down", "not_connected", "unknown"]
)
def test_a_failed_app_that_is_not_healthy_stays_failed(
    factory, notify, status
):
    add(factory)

    recover(health(status))

    row = read(factory)
    assert row.status == DeploymentStatus.FAILED
    assert row.step_message == "Deployment interrupted by a CMP restart."
    notify.assert_not_called()


@pytest.mark.parametrize(
    "fields",
    [
        {"terraform_outputs": None},
        {"step_message": "Deletion failed: boom"},
        {"template_id": "import-service"},
        {"provider_type": ProviderType.LEGACY_HYBRID},
    ],
    ids=["no_outputs", "deletion", "imported", "legacy"],
)
def test_only_finished_deployments_are_promoted(factory, notify, fields):
    add(factory, **fields)
    reader = health("healthy")

    recover(reader)

    reader.assert_not_awaited()
    assert read(factory).status == DeploymentStatus.FAILED


def test_a_health_error_does_not_stop_the_others(factory, notify):
    add(factory, "first")
    add(factory, "second")

    recover(health(RuntimeError("kube down"), "healthy"))

    assert read(factory, "first").status == DeploymentStatus.FAILED
    assert read(factory, "second").status == DeploymentStatus.RUNNING
