import re
from dataclasses import dataclass

_LOCK_ERROR_MARKER = "Error acquiring the state lock"
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
_LOCK_FIELD_PATTERN = re.compile(
    r"^[\s│]*(ID|Path|Operation|Who|Version|Created):\s*(.+?)\s*$",
    re.MULTILINE,
)
_OPERATION_PREFIX = "OperationType"


@dataclass(frozen=True)
class StateLockInfo:
    lock_id: str = ""
    operation: str = ""
    who: str = ""
    created: str = ""


class StateLockedError(RuntimeError):
    def __init__(self, info: StateLockInfo):
        self.info = info
        super().__init__(format_state_lock_message(info))


def format_state_lock_message(info: StateLockInfo) -> str:
    holder = f" by {info.who}" if info.who else ""
    since = f" at {info.created}" if info.created else ""
    operation = f" ({info.operation})" if info.operation else ""
    return (
        f"Another operation is already running on this state{operation}, "
        f"started{holder}{since}. Retry later."
    )


def parse_state_lock(terraform_output: str) -> StateLockInfo | None:
    """Return the lock holder when Terraform refused to lock, else None."""
    plain_output = _ANSI_ESCAPE_PATTERN.sub("", terraform_output)
    if _LOCK_ERROR_MARKER not in plain_output:
        return None

    fields = dict(_LOCK_FIELD_PATTERN.findall(plain_output))
    operation = fields.get("Operation", "").removeprefix(_OPERATION_PREFIX)
    return StateLockInfo(
        lock_id=fields.get("ID", ""),
        operation=operation.lower(),
        who=fields.get("Who", ""),
        created=fields.get("Created", ""),
    )


def raise_if_state_locked(terraform_output: str) -> None:
    info = parse_state_lock(terraform_output)
    if info is not None:
        raise StateLockedError(info)
