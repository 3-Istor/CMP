"""
Calls per assistant per minute. In memory: the CMP runs one replica, and a
restart resetting the counters is harmless.
"""

import time
from collections import defaultdict, deque

READS_PER_MINUTE = 120
WRITES_PER_MINUTE = 20

_calls: dict[tuple[int, str], deque] = defaultdict(deque)


class RateLimited(Exception):
    pass


def check(grant_id: int, kind: str) -> None:
    limit = WRITES_PER_MINUTE if kind == "write" else READS_PER_MINUTE
    window = _calls[(grant_id, kind)]
    now = time.monotonic()
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        raise RateLimited(
            f"Too many {kind} calls: at most {limit} per minute. Slow down "
            "and batch changes (push_files takes several files at once)."
        )
    window.append(now)
