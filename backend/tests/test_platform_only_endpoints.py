import asyncio
from unittest import mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.project import Project
from app.routers import catalog
from app.services import authz, keycloak_service

ADMIN = {
    "sub": "a1",
    "preferred_username": "root",
    "groups": ["infra"],
}
MEMBER = {"sub": "m1", "preferred_username": "dev"}


class TestCatalogSync:
    def test_refuses_a_user_who_is_not_a_platform_admin(self):
        with mock.patch.object(catalog, "get_repository") as repository:
            with pytest.raises(HTTPException) as exc:
                asyncio.run(catalog.sync_templates(MEMBER))

        assert exc.value.status_code == 403
        repository.return_value.force_sync.assert_not_called()

    def test_syncs_for_a_platform_admin(self):
        with mock.patch.object(catalog, "get_repository") as repository:
            asyncio.run(catalog.sync_templates(ADMIN))

        repository.return_value.force_sync.assert_called_once()


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(Project(project_name="shop", owner_username="dev"))
        db.commit()
    with mock.patch.object(authz, "SessionLocal", factory):
        yield factory


class TestRequireProjectMember:
    def test_owner_outside_the_admins_group_still_acts_as_admin(
        self, session_factory
    ):
        with mock.patch.object(
            keycloak_service, "get_project_role", lambda u, p: None
        ):
            role = authz.require_project_member(MEMBER, "shop", True)

        assert role == "admin"

    def test_refuses_a_member_where_admin_is_required(self, session_factory):
        with mock.patch.object(
            keycloak_service, "get_project_role", lambda u, p: "member"
        ):
            with pytest.raises(HTTPException) as exc:
                authz.require_project_member(
                    {"sub": "x", "preferred_username": "other"}, "shop", True
                )

        assert exc.value.status_code == 403


class TestPlatformIdentitiesOnProjects:
    @pytest.mark.parametrize(
        ("groups", "expected"),
        [
            (["infra"], "admin"),
            (["security-auditor"], "guest"),
            (["finops"], "guest"),
            (["member"], None),
        ],
    )
    def test_role_on_a_project_the_caller_is_not_a_member_of(
        self, session_factory, groups, expected
    ):
        token = {"sub": "x", "preferred_username": "other", "groups": groups}

        with mock.patch.object(
            keycloak_service, "get_project_role", lambda u, p: None
        ):
            role = authz.project_role_or_none(token, "shop")

        assert role == expected

    def test_auditor_cannot_act_as_a_member(self, session_factory):
        token = {
            "sub": "x",
            "preferred_username": "aud",
            "groups": ["security-auditor"],
        }

        with mock.patch.object(
            keycloak_service, "get_project_role", lambda u, p: None
        ):
            with pytest.raises(HTTPException) as exc:
                authz.require_project_member(token, "shop")

        assert exc.value.status_code == 403
