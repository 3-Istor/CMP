import asyncio
import contextlib
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
from app.services import keycloak_admin, keycloak_service, vault_auth
from app.services.clusters import state as cluster_state
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
        mock.patch.object(keycloak_admin, "settings", settings),
        mock.patch.object(vault_auth, "settings", settings),
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


def create(db, name="test-p", cloud="onprem", user="alice", tasks=None):
    return asyncio.run(
        projects_router.create_project(
            ProjectCreate(project_name=name, target_cloud=cloud),
            BackgroundTasks() if tasks is None else tasks,
            {"sub": f"id-{user}", "preferred_username": user},
            db,
        )
    )


def seed(
    db, owner="bob", state=ProjectStatus.FAILED, cloud=TargetCloud.ONPREM
):
    db.add(
        Project(
            project_name="test-p",
            owner_username=owner,
            target_cloud=cloud,
            status=state,
        )
    )
    db.commit()


def group_role(role):
    return mock.patch.object(
        keycloak_service, "get_project_role", lambda user_id, project: role
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


def test_retry_by_a_user_without_a_role_on_the_project_is_refused(db, seams):
    seed(db, owner="bob")

    with group_role(None), pytest.raises(HTTPException) as excinfo:
        create(db, user="alice")

    assert excinfo.value.status_code == 403
    seams.publish.assert_not_called()


@pytest.mark.parametrize(
    "state", [s for s in ProjectStatus if s is not ProjectStatus.FAILED]
)
def test_only_a_failed_project_can_be_retried(db, seams, state):
    seed(db, owner="alice", state=state)
    tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as excinfo:
        create(db, user="alice", tasks=tasks)

    assert excinfo.value.status_code == 409
    seams.publish.assert_not_called()
    assert tasks.tasks == []


def test_retry_keeps_the_owner_and_the_cloud_of_the_first_attempt(db, seams):
    seed(db, owner="bob", cloud=TargetCloud.GCP)
    tasks = BackgroundTasks()

    with (
        group_role("admin"),
        _states(aws="up", gcp="up"),
        mock.patch.object(
            projects_router, "run_project_bootstrap", return_value=True
        ) as bootstrap,
        mock.patch.object(projects_router, "add_user_to_project") as grant,
        mock.patch.object(
            projects_router, "sync_project_org", new=mock.AsyncMock()
        ) as org,
    ):
        create(db, cloud="public", user="carol", tasks=tasks)
        asyncio.run(tasks())

    publish = seams.publish.await_args.kwargs
    assert publish["owner_username"] == "bob"
    assert publish["target_cloud"] == TargetCloud.GCP
    bootstrap.assert_called_once_with(
        project_name="test-p", target_cloud="gcp"
    )
    grant.assert_called_once_with("bob", "test-p", "admin")
    org.assert_awaited_once_with("test-p", "bob")


@contextlib.contextmanager
def _states(**by_cloud):
    def read(cloud):
        return by_cloud[cloud.value]

    with (
        mock.patch.object(projects_router, "fresh_state", read),
        mock.patch.object(cluster_state, "fresh_state", read),
    ):
        yield


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
def test_explicit_cloud_is_kept_when_its_cluster_is_up(db, seams, cloud):
    with _states(aws="up", gcp="up"):
        created = create(db, cloud=cloud)

    assert created.target_cloud == TargetCloud(cloud)


def test_onprem_project_does_not_read_cluster_states(db, seams):
    with mock.patch.object(projects_router, "fresh_state") as states:
        create(db, cloud="onprem")

    states.assert_not_called()


@pytest.mark.parametrize("state", ["down", "starting"])
def test_explicit_cloud_is_refused_when_its_cluster_is_not_up(
    db, seams, state
):
    with _states(aws=state, gcp="up"):
        with pytest.raises(HTTPException) as excinfo:
            create(db, cloud="aws")

    assert excinfo.value.status_code == 409
    seams.publish.assert_not_called()
