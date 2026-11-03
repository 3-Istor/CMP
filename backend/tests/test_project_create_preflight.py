import asyncio
from unittest import mock

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.project import Project, ProjectStatus, TargetCloud
from app.routers import projects as projects_router
from app.schemas.project import ProjectCreate
from app.services.project_registry import (
    NameCollisionError,
    ProtectedRecordError,
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
        yield session


@pytest.fixture
def seams():
    with (
        mock.patch.object(projects_router, "settings") as settings,
        mock.patch.object(projects_router, "find_leftovers") as leftovers,
        mock.patch.object(
            projects_router, "ensure_project_names_free", new=mock.AsyncMock()
        ) as names,
        mock.patch.object(
            projects_router, "publish_record", new=mock.AsyncMock()
        ) as publish,
    ):
        leftovers.return_value = []
        yield mock.Mock(
            leftovers=leftovers,
            names=names,
            publish=publish,
            settings=settings,
        )


def create(db, name="test-p", cloud="onprem"):
    return asyncio.run(
        projects_router.create_project(
            ProjectCreate(project_name=name, target_cloud=cloud),
            BackgroundTasks(),
            {"sub": "u1", "preferred_username": "alice"},
            db,
        )
    )


def test_create_project_returns_409_listing_leftovers_before_publishing(
    db, seams
):
    seams.leftovers.return_value = [
        "Cloudflare DNS record 'status-test-p.3istor.com'",
        "Keycloak client 'broker-test-p' in realm '3istor'",
    ]

    with pytest.raises(HTTPException) as excinfo:
        create(db)

    assert excinfo.value.status_code == 409
    assert "status-test-p.3istor.com" in excinfo.value.detail
    assert "broker-test-p" in excinfo.value.detail
    seams.publish.assert_not_called()


def test_create_project_returns_409_on_name_collision_before_publishing(
    db, seams
):
    seams.names.side_effect = NameCollisionError(
        ["namespace 'foo-bar-system' collides with project 'foo'"]
    )

    with pytest.raises(HTTPException) as excinfo:
        create(db, "foo-bar")

    assert excinfo.value.status_code == 409
    assert "project 'foo'" in excinfo.value.detail
    seams.publish.assert_not_called()


def test_create_project_returns_409_when_the_record_is_deletion_protected(
    db, seams
):
    seams.publish.side_effect = ProtectedRecordError(
        "Project 'test-p' is deletion-protected: turn protection off "
        "before re-creating it."
    )

    with pytest.raises(HTTPException) as excinfo:
        create(db)

    assert excinfo.value.status_code == 409
    assert "deletion-protected" in excinfo.value.detail
    assert db.query(Project).count() == 0


def test_create_project_skips_the_leftovers_check_when_retrying_a_known_project(
    db, seams
):
    db.add(
        Project(
            project_name="test-p",
            owner_username="alice",
            target_cloud=TargetCloud.ONPREM,
            status=ProjectStatus.FAILED,
        )
    )
    db.commit()

    create(db)

    seams.leftovers.assert_not_called()
    seams.publish.assert_awaited_once()


def _states(**by_cloud):
    return mock.patch.object(
        projects_router, "fresh_state", lambda c: by_cloud[c.value]
    )


def test_public_project_goes_to_the_other_cloud_than_the_last_one(db, seams):
    db.add(
        Project(
            project_name="old",
            owner_username="bob",
            target_cloud=TargetCloud.AWS,
        )
    )
    db.add(
        Project(
            project_name="lab",
            owner_username="bob",
            target_cloud=TargetCloud.ONPREM,
        )
    )
    db.commit()

    with _states(aws="up", gcp="up"):
        created = create(db, cloud="public")

    assert created.target_cloud == TargetCloud.GCP
    assert seams.publish.await_args.kwargs["target_cloud"] == TargetCloud.GCP
    row = db.query(Project).filter(Project.project_name == "test-p").one()
    assert row.target_cloud == TargetCloud.GCP


def test_public_project_is_refused_when_no_public_cloud_is_up(db, seams):
    with _states(aws="down", gcp="starting"):
        with pytest.raises(HTTPException) as excinfo:
            create(db, cloud="public")

    assert excinfo.value.status_code == 409
    seams.publish.assert_not_called()


@pytest.mark.parametrize("cloud", ["onprem", "aws", "gcp"])
def test_explicit_cloud_is_kept_without_reading_cluster_states(
    db, seams, cloud
):
    with mock.patch.object(projects_router, "fresh_state") as states:
        created = create(db, cloud=cloud)

    assert created.target_cloud == TargetCloud(cloud)
    states.assert_not_called()
