import logging
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")

_LOCK_ERROR_MARKER = "Error acquiring the state lock"
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
_LOCK_FIELD_PATTERN = re.compile(
    r"^[\s│]*(ID|Path|Operation|Who|Version|Created):\s*(.+?)\s*$",
    re.MULTILINE,
)
_OPERATION_PREFIX = "OperationType"
# Host part of Who in a pod: <deployment>-<replicaset hash>-<pod suffix>.
_POD_NAME = re.compile(
    r"^(?P<deployment>[a-z0-9-]+)-[a-z0-9]{6,10}-[a-z0-9]{5}$"
)


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


def held_by_dead_pod(info: StateLockInfo, hostname: str | None = None) -> bool:
    """Whether the lock was left by another pod of this backend's Deployment.

    The backend runs one replica with the Recreate strategy: any other pod of
    its Deployment has stopped, and its Terraform run died with it.
    """
    here = _POD_NAME.match(hostname or socket.gethostname())
    holder = _POD_NAME.match(info.who.rpartition("@")[2])
    return bool(
        here
        and holder
        and holder.group(0) != here.group(0)
        and holder["deployment"] == here["deployment"]
    )


def run_releasing_dead_pod_lock(
    run: Callable[[list[str]], T], command: list[str]
) -> T:
    """Run a Terraform command. A lock left by a dead backend pod is released
    and the command run once more; any other lock is reported as is."""
    try:
        return run(command)
    except StateLockedError as exc:
        if not exc.info.lock_id or not held_by_dead_pod(exc.info):
            raise
        logger.warning(
            "Releasing state lock %s left by %s, a backend pod that is gone",
            exc.info.lock_id,
            exc.info.who,
        )
        run(["terraform", "force-unlock", "-force", exc.info.lock_id])
        return run(command)
