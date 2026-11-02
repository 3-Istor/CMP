import asyncio
from unittest import mock

import pytest
from fastapi import BackgroundTasks

from app.models.project import Project, ProjectStatus, TargetCloud
from app.routers import projects as projects_router
from app.schemas.project import ProjectCreate
from app.services import grafana_service, keycloak_service
from tests.test_app_security_data import session_factory  # noqa: F401
from tests.test_project_create_preflight import db, seams  # noqa: F401


@pytest.fixture
def grafana_add():
    add = mock.AsyncMock(return_value=True)
    with (
        mock.patch.object(grafana_service, "add_user_to_project_org", add),
        mock.patch.object(projects_router, "add_user_to_project_org", add),
    ):
        yield add


def members(*pairs):
    return [{"username": u, "role": r} for u, r in pairs]


def test_bootstrap_success_adds_every_project_member_to_the_org(
    db, seams, grafana_add
):
    tasks = BackgroundTasks()
    with (
        mock.patch.object(
            projects_router, "run_project_bootstrap", return_value=True
        ),
        mock.patch.object(projects_router, "add_user_to_project"),
        mock.patch.object(
            keycloak_service,
            "list_project_members",
            return_value=members(("alice", "admin"), ("bob", "member")),
        ),
    ):
        asyncio.run(
            projects_router.create_project(
                ProjectCreate(
                    project_name="test-p", target_cloud=TargetCloud.ONPREM
                ),
                tasks,
                {"sub": "u1", "preferred_username": "alice"},
                db,
            )
        )
        asyncio.run(tasks())

    added = {c.args for c in grafana_add.await_args_list}
    assert ("test-p", "bob", "member") in added
    assert any(a[:2] == ("test-p", "alice") for a in added)


def test_startup_sync_adds_owners_and_members_of_active_projects(
    session_factory, grafana_add
):
    with session_factory() as s:
        s.add_all(
            [
                Project(project_name="test-hugo", owner_username="hugo"),
                Project(
                    project_name="booting",
                    owner_username="carol",
                    status=ProjectStatus.PROVISIONING,
                ),
            ]
        )
        s.commit()

    with (
        mock.patch.object(grafana_service, "SessionLocal", session_factory),
        mock.patch.object(
            keycloak_service,
            "list_project_members",
            return_value=members(("bob", "guest")),
        ),
    ):
        asyncio.run(grafana_service.sync_all_project_orgs())

    assert {c.args for c in grafana_add.await_args_list} == {
        ("test-hugo", "hugo", "owner"),
        ("test-hugo", "bob", "guest"),
    }


def test_startup_sync_logs_and_moves_on_when_keycloak_is_down(
    session_factory, grafana_add
):
    with session_factory() as s:
        s.add_all(
            [
                Project(project_name="first", owner_username="ann"),
                Project(project_name="second", owner_username="ben"),
            ]
        )
        s.commit()

    def list_members(project_name):
        if project_name == "first":
            raise ConnectionError("keycloak down")
        return []

    with (
        mock.patch.object(grafana_service, "SessionLocal", session_factory),
        mock.patch.object(
            keycloak_service, "list_project_members", list_members
        ),
    ):
        asyncio.run(grafana_service.sync_all_project_orgs())

    grafana_add.assert_awaited_once_with("second", "ben", "owner")
