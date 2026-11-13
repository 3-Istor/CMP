from unittest import mock

import pytest

from app.services import state_lock
from app.services.state_lock import (
    StateLockedError,
    StateLockInfo,
    held_by_dead_pod,
    parse_state_lock,
    raise_if_state_locked,
    run_releasing_dead_pod_lock,
)
from app.services.terraform_executor import TerraformExecutor

LOCKED_OUTPUT = """
Error: Error acquiring the state lock

Error message: operation error S3: PutObject, https response error
StatusCode: 412, api error PreconditionFailed: At least one of the
pre-conditions you specified did not hold
Lock Info:
  ID:        3f1c0a52-9b1e-4d3a-8a77-2c1d5b0e9f11
  Path:      istor-cnp-tfstate-757826/cmp/aws/projects/p/bootstrap.tfstate
  Operation: OperationTypeApply
  Who:       app@arcl-cmp-backend-fd6757c6b-z68tw
  Version:   1.11.4
  Created:   2026-10-06 13:50:04.304 +0000 UTC
  Info:

Terraform acquires a state lock to protect the state from being written
by multiple users at the same time.
"""


COLORED_BOXED_OUTPUT = (
    "\x1b[31m╷\x1b[0m\x1b[0m\n"
    "\x1b[31m│\x1b[0m \x1b[0m\x1b[1m\x1b[31mError: \x1b[0m\x1b[0m"
    "\x1b[1mError acquiring the state lock\x1b[0m\n"
    "\x1b[31m│\x1b[0m \x1b[0mLock Info:\n"
    "\x1b[31m│\x1b[0m \x1b[0m  ID:        88afe54a-383c-70fc-6560-9db4c8f89a57\n"
    "\x1b[31m│\x1b[0m \x1b[0m  Operation: OperationTypeDestroy\n"
    "\x1b[31m│\x1b[0m \x1b[0m  Who:       alice@laptop\n"
    "\x1b[31m│\x1b[0m \x1b[0m  Created:   2026-10-06 14:00:07.77 +0000 UTC\n"
)


def test_parse_state_lock_reads_colored_boxed_terraform_output():
    info = parse_state_lock(COLORED_BOXED_OUTPUT)

    assert info is not None
    assert info.who == "alice@laptop"
    assert info.operation == "destroy"
    assert info.created == "2026-10-06 14:00:07.77 +0000 UTC"


def test_parse_state_lock_returns_none_when_output_has_no_lock_error():
    assert parse_state_lock("Error: Invalid provider configuration") is None


def test_parse_state_lock_extracts_holder_operation_and_date():
    info = parse_state_lock(LOCKED_OUTPUT)

    assert info is not None
    assert info.who == "app@arcl-cmp-backend-fd6757c6b-z68tw"
    assert info.operation == "apply"
    assert info.created == "2026-10-06 13:50:04.304 +0000 UTC"
    assert info.lock_id == "3f1c0a52-9b1e-4d3a-8a77-2c1d5b0e9f11"


def test_raise_if_state_locked_message_tells_user_to_retry_later():
    with pytest.raises(StateLockedError) as raised:
        raise_if_state_locked(LOCKED_OUTPUT)

    message = str(raised.value)
    assert "already running" in message
    assert "app@arcl-cmp-backend-fd6757c6b-z68tw" in message
    assert "2026-10-06 13:50:04.304 +0000 UTC" in message
    assert message.endswith("Retry later.")


def test_raise_if_state_locked_does_nothing_for_other_errors():
    raise_if_state_locked("Error: Unsupported argument")


def test_state_locked_message_stays_within_step_message_column():
    with pytest.raises(StateLockedError) as raised:
        raise_if_state_locked(LOCKED_OUTPUT)

    assert len(f"❌ Deployment failed: {raised.value}"[:222]) <= 255


LOCK_ID = "3f1c0a52-9b1e-4d3a-8a77-2c1d5b0e9f11"
THIS_POD = "arcl-cmp-backend-7c9d5b8f4-k2x8p"
DEAD_POD_LOCK = StateLockInfo(
    lock_id=LOCK_ID, who="app@arcl-cmp-backend-fd6757c6b-z68tw"
)


@pytest.mark.parametrize(
    "who, expected",
    [
        ("app@arcl-cmp-backend-fd6757c6b-z68tw", True),
        (f"app@{THIS_POD}", False),
        ("alice@laptop", False),
        ("app@arcl-cmp-staging-backend-fd6757c6b-z68tw", False),
        ("", False),
    ],
)
def test_only_another_pod_of_the_backend_deployment_counts_as_dead(
    who, expected
):
    info = StateLockInfo(lock_id=LOCK_ID, who=who)

    assert held_by_dead_pod(info, hostname=THIS_POD) is expected


def test_outside_a_pod_no_lock_counts_as_dead():
    assert held_by_dead_pod(DEAD_POD_LOCK, hostname="laptop") is False


def test_lock_of_a_dead_pod_is_released_and_the_command_run_once_more():
    run = mock.Mock(side_effect=[StateLockedError(DEAD_POD_LOCK), None, "ok"])

    with mock.patch.object(
        state_lock.socket, "gethostname", return_value=THIS_POD
    ):
        result = run_releasing_dead_pod_lock(run, ["terraform", "apply"])

    assert result == "ok"
    assert [c.args[0] for c in run.call_args_list] == [
        ["terraform", "apply"],
        ["terraform", "force-unlock", "-force", LOCK_ID],
        ["terraform", "apply"],
    ]


def test_a_lock_still_held_after_the_release_is_reported():
    run = mock.Mock(
        side_effect=[
            StateLockedError(DEAD_POD_LOCK),
            None,
            StateLockedError(
                StateLockInfo(lock_id="other", who="alice@laptop")
            ),
        ]
    )

    with mock.patch.object(
        state_lock.socket, "gethostname", return_value=THIS_POD
    ):
        with pytest.raises(StateLockedError):
            run_releasing_dead_pod_lock(run, ["terraform", "apply"])

    assert run.call_count == 3


def test_a_lock_held_by_a_person_is_never_released():
    human = StateLockInfo(lock_id=LOCK_ID, who="alice@laptop")
    run = mock.Mock(side_effect=StateLockedError(human))

    with mock.patch.object(
        state_lock.socket, "gethostname", return_value=THIS_POD
    ):
        with pytest.raises(StateLockedError):
            run_releasing_dead_pod_lock(run, ["terraform", "apply"])

    run.assert_called_once_with(["terraform", "apply"])


def test_app_deployment_commands_release_a_lock_left_by_a_dead_pod(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    executor = TerraformExecutor(tmp_path, "web")

    with mock.patch.object(
        TerraformExecutor,
        "_run_command_once",
        side_effect=[StateLockedError(DEAD_POD_LOCK), None, "ok"],
    ) as once, mock.patch.object(
        state_lock.socket, "gethostname", return_value=THIS_POD
    ):
        assert executor._run_command(["terraform", "apply"]) == "ok"

    assert [c.args[0] for c in once.call_args_list] == [
        ["terraform", "apply"],
        ["terraform", "force-unlock", "-force", LOCK_ID],
        ["terraform", "apply"],
    ]


def test_apps_with_the_same_name_in_two_projects_get_separate_terraform_data_dirs(
    tmp_path, monkeypatch
):
    # Arrange
    monkeypatch.chdir(tmp_path)

    # Act
    first = TerraformExecutor(tmp_path, "web", "cnp/projects/proj-a/web")
    second = TerraformExecutor(tmp_path, "web", "cnp/projects/proj-b/web")

    # Assert
    assert first.state_dir.resolve() != second.state_dir.resolve()


def test_an_apps_staging_gets_its_own_terraform_data_dir(
    tmp_path, monkeypatch
):
    # Arrange
    monkeypatch.chdir(tmp_path)

    # Act
    app = TerraformExecutor(tmp_path, "web", "cnp/projects/proj-a/web")
    staging = TerraformExecutor(
        tmp_path, "web-staging", "cnp/projects/proj-a/web/staging"
    )

    # Assert
    assert (app.state_dir / ".terraform").resolve() != (
        staging.state_dir / ".terraform"
    ).resolve()
