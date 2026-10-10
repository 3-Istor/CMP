import asyncio
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.project import Project, ProjectStatus, TargetCloud
from app.routers import projects as projects_router
from app.services import project_bootstrap, project_status
from app.services.project_bootstrap import _failure_message
from app.services.state_lock import StateLockedError, StateLockInfo


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with mock.patch.object(project_status, "SessionLocal", factory):
        yield factory


def add_project(factory, name, owner="alice", status=ProjectStatus.ACTIVE):
    with factory() as db:
        db.add(
            Project(
                project_name=name,
                owner_username=owner,
                target_cloud=TargetCloud.AWS,
                status=status,
            )
        )
        db.commit()


def read_project(factory, name):
    with factory() as db:
        return db.query(Project).filter_by(project_name=name).one()


def test_set_project_status_records_status_and_message(session_factory):
    add_project(session_factory, "demo")

    project_status.set_project_status(
        "demo", ProjectStatus.PROVISIONING, "Applying Terraform"
    )

    row = read_project(session_factory, "demo")
    assert row.status == ProjectStatus.PROVISIONING
    assert row.step_message == "Applying Terraform"


def test_set_project_status_clears_message_when_none_given(session_factory):
    add_project(session_factory, "demo")
    project_status.set_project_status("demo", ProjectStatus.FAILED, "boom")

    project_status.set_project_status("demo", ProjectStatus.ACTIVE)

    assert read_project(session_factory, "demo").step_message is None


def test_set_project_status_truncates_message_to_column_size(session_factory):
    add_project(session_factory, "demo")

    project_status.set_project_status("demo", ProjectStatus.FAILED, "x" * 1000)

    assert len(read_project(session_factory, "demo").step_message) == 255


def test_set_project_status_ignores_unknown_project(session_factory):
    project_status.set_project_status("ghost", ProjectStatus.FAILED, "boom")


def test_delete_project_row_removes_the_project(session_factory):
    add_project(session_factory, "demo")

    project_status.delete_project_row("demo")

    with session_factory() as db:
        assert db.query(Project).count() == 0


def test_failure_message_spells_out_a_held_state_lock():
    lock = StateLockedError(
        StateLockInfo(operation="apply", who="bob@pod", created="14:00")
    )

    message = _failure_message("Bootstrap", "while applying Terraform", lock)

    assert "already running" in message
    assert "bob@pod" in message


def test_failure_message_hides_raw_terraform_output():
    message = _failure_message(
        "Teardown",
        "while destroying Terraform",
        RuntimeError("token ghs_secret leaked in stderr"),
    )

    assert "ghs_secret" not in message
    assert message.startswith("Teardown failed while destroying Terraform")


def list_for(factory, username, keycloak_projects):
    token = {"preferred_username": username}
    with factory() as db, mock.patch.object(
        projects_router,
        "fetch_user_projects_from_keycloak",
        return_value=keycloak_projects,
    ), mock.patch.object(
        projects_router, "get_user_id_from_token", return_value="uid"
    ):
        return asyncio.run(projects_router.list_projects(token, db))


def test_list_projects_shows_owner_a_failed_bootstrap_without_keycloak_groups(
    session_factory,
):
    add_project(session_factory, "broken", "alice", ProjectStatus.FAILED)

    listed = list_for(session_factory, "alice", [])

    assert [(p.name, p.status, p.role, p.is_accessible) for p in listed] == [
        ("broken", ProjectStatus.FAILED, "owner", False)
    ]


def test_list_projects_hides_another_users_failed_bootstrap(session_factory):
    add_project(session_factory, "broken", "alice", ProjectStatus.FAILED)

    assert list_for(session_factory, "bob", []) == []


def test_list_projects_reports_status_of_projects_already_in_keycloak(
    session_factory,
):
    add_project(
        session_factory, "tearing", "alice", ProjectStatus.DECOMMISSIONING
    )

    listed = list_for(
        session_factory, "alice", [{"name": "tearing", "role": "admin"}]
    )

    assert [(p.name, p.status) for p in listed] == [
        ("tearing", ProjectStatus.DECOMMISSIONING)
    ]


@pytest.fixture
def stubbed_terraform(session_factory, tmp_path):
    with mock.patch.object(
        project_bootstrap, "_get_module_path", return_value=tmp_path
    ), mock.patch.object(
        project_bootstrap, "_stage_module", return_value=tmp_path
    ), mock.patch.object(
        project_bootstrap, "get_installation_token"
    ), mock.patch.object(
        project_bootstrap.settings, "GITHUB_INSTALLATION_ID", ""
    ), mock.patch.object(
        project_bootstrap, "_terraform_init"
    ) as init, mock.patch.object(
        project_bootstrap, "_run"
    ) as run, mock.patch.object(
        project_bootstrap,
        "set_project_status",
        wraps=project_status.set_project_status,
    ), mock.patch.object(
        project_bootstrap,
        "delete_project_row",
        wraps=project_status.delete_project_row,
    ):
        yield init, run


def test_bootstrap_marks_project_active_and_clears_message(
    session_factory, stubbed_terraform
):
    add_project(session_factory, "demo", status=ProjectStatus.PROVISIONING)

    succeeded = project_bootstrap.run_project_bootstrap("demo", "aws")

    row = read_project(session_factory, "demo")
    assert succeeded is True
    assert (row.status, row.step_message) == (ProjectStatus.ACTIVE, None)


def test_bootstrap_fails_with_lock_holder_when_state_is_locked(
    session_factory, stubbed_terraform
):
    add_project(session_factory, "demo", status=ProjectStatus.PROVISIONING)
    _, run = stubbed_terraform
    run.side_effect = StateLockedError(
        StateLockInfo(operation="apply", who="bob@pod", created="14:00")
    )

    succeeded = project_bootstrap.run_project_bootstrap("demo", "aws")

    row = read_project(session_factory, "demo")
    assert succeeded is False
    assert row.status == ProjectStatus.FAILED
    assert "bob@pod" in row.step_message


def test_bootstrap_fails_with_generic_message_on_other_errors(
    session_factory, stubbed_terraform
):
    add_project(session_factory, "demo", status=ProjectStatus.PROVISIONING)
    _, run = stubbed_terraform
    run.side_effect = RuntimeError("boom with a secret")

    project_bootstrap.run_project_bootstrap("demo", "aws")

    row = read_project(session_factory, "demo")
    assert row.status == ProjectStatus.FAILED
    assert "secret" not in row.step_message


def test_teardown_removes_the_project_row_when_destroy_succeeds(
    session_factory, stubbed_terraform
):
    add_project(session_factory, "demo", status=ProjectStatus.DECOMMISSIONING)

    project_bootstrap.run_project_teardown("demo", "aws")

    with session_factory() as db:
        assert db.query(Project).count() == 0


def test_teardown_keeps_the_row_as_decommission_failed_when_destroy_fails(
    session_factory, stubbed_terraform
):
    add_project(session_factory, "demo", status=ProjectStatus.DECOMMISSIONING)
    _, run = stubbed_terraform
    run.side_effect = RuntimeError("boom")

    project_bootstrap.run_project_teardown("demo", "aws")

    row = read_project(session_factory, "demo")
    assert row.status == ProjectStatus.DECOMMISSION_FAILED
    assert row.step_message.startswith("Teardown failed")


def delete_as(factory, username, project_name, groups_exist):
    token = {"preferred_username": username}
    background = mock.Mock()
    admin_group = {"id": "g1"} if groups_exist else None
    with factory() as db, mock.patch(
        "app.services.keycloak_service._get_admin_token", return_value="t"
    ), mock.patch(
        "app.services.keycloak_service._find_group_by_name",
        return_value=admin_group,
    ), mock.patch(
        "app.services.keycloak_service._check_user_in_group_realtime",
        return_value=True,
    ), mock.patch.object(
        projects_router, "get_user_id_from_token", return_value="uid"
    ), mock.patch.object(
        projects_router, "remove_record", new=mock.AsyncMock()
    ):
        asyncio.run(
            projects_router.delete_project(project_name, background, token, db)
        )
    return background


def test_owner_can_retry_a_failed_teardown_after_the_groups_are_gone(
    session_factory,
):
    add_project(
        session_factory, "demo", "alice", ProjectStatus.DECOMMISSION_FAILED
    )

    background = delete_as(
        session_factory, "alice", "demo", groups_exist=False
    )

    background.add_task.assert_called_once()
    assert (
        read_project(session_factory, "demo").status
        == ProjectStatus.DECOMMISSIONING
    )


def test_other_users_get_not_found_for_a_failed_teardown_without_groups(
    session_factory,
):
    add_project(
        session_factory, "demo", "alice", ProjectStatus.DECOMMISSION_FAILED
    )

    with pytest.raises(projects_router.HTTPException) as raised:
        delete_as(session_factory, "bob", "demo", groups_exist=False)

    assert raised.value.status_code == 404


def test_a_project_without_groups_is_not_found_unless_its_teardown_failed(
    session_factory,
):
    add_project(session_factory, "demo", "alice", ProjectStatus.ACTIVE)

    with pytest.raises(projects_router.HTTPException) as raised:
        delete_as(session_factory, "alice", "demo", groups_exist=False)

    assert raised.value.status_code == 404
