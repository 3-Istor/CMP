import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.project import Project, ProjectStatus, TargetCloud
from app.routers import projects as projects_router
from app.schemas.project import AddMemberRequest


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Project(
            project_name="demo",
            owner_username="alice",
            status=ProjectStatus.ACTIVE,
            target_cloud=TargetCloud.ONPREM,
        )
    )
    session.commit()
    return session


def test_the_owner_role_cannot_be_changed_by_adding_them_again(db):
    with mock.patch.object(
        projects_router, "remove_user_from_project"
    ) as remove:
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(
                projects_router.add_project_member(
                    AddMemberRequest(username="alice", role="guest"),
                    SimpleNamespace(project_name="demo"),
                    db,
                )
            )

    assert excinfo.value.status_code == 400
    remove.assert_not_called()
