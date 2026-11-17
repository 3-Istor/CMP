"""
The last topology built per project, for a short while (CNP-242).

Every member with the map open polls it: one build reads the registry,
Argo CD, every app namespace and VictoriaMetrics. The writers that change
the map (links, import deploys and removals) invalidate it. Kept apart from
``topology`` so those writers do not import the map builder.
"""

import time

# 1.5 x the map's 30 s poll (POLL_MS in TopologyView.tsx): the next poll is
# served from here with half a period of slack, the one after rebuilds.
TTL_SECONDS = 45.0

_entries: dict[str, tuple[float, dict]] = {}
_generations: dict[str, int] = {}


def get(project: str) -> dict | None:
    entry = _entries.get(project)
    if entry is None or entry[0] <= time.monotonic():
        return None
    return entry[1]


def generation(project: str) -> int:
    return _generations.get(project, 0)


def put(project: str, topology: dict, built_at_generation: int) -> None:
    """A build that overlapped an invalidation is not kept: it may be stale."""
    if generation(project) != built_at_generation:
        return
    _entries[project] = (time.monotonic() + TTL_SECONDS, topology)


def invalidate(project: str) -> None:
    _generations[project] = generation(project) + 1
    _entries.pop(project, None)


def clear() -> None:
    _entries.clear()
    _generations.clear()
