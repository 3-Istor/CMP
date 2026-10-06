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
from app.services.project_registry import NameCollisionError


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


def create(db, name="test-p"):
    return asyncio.run(
        projects_router.create_project(
            ProjectCreate(project_name=name, target_cloud=TargetCloud.ONPREM),
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
