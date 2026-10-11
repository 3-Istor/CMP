import pytest

from app.services.state_lock import (
    StateLockedError,
    parse_state_lock,
    raise_if_state_locked,
)

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
