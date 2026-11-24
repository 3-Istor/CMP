"""
A small in-process cache with stale-while-revalidate, for reads that many
pages poll (observability summaries, capabilities).

A fresh entry is served as is. A stale one (older than *ttl*) is still served
while one background task refreshes it, so a poll never waits on the stores.
Past *max_stale* an entry is too old to show and the caller waits for the
load. Concurrent misses on one key share a single load.
"""

import asyncio
import logging
import time
import weakref
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

logger = logging.getLogger(__name__)

_instances: "weakref.WeakSet[SwrCache]" = weakref.WeakSet()


class SwrCache:
    def __init__(
        self,
        name: str,
        ttl: float,
        max_stale: float | None = None,
        max_entries: int = 1024,
    ):
        self.name = name
        self.ttl = ttl
        self.max_stale = max_stale if max_stale is not None else 10 * ttl
        self.max_entries = max_entries
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._loads: dict[Hashable, asyncio.Future] = {}
        _instances.add(self)

    async def get(
        self, key: Hashable, load: Callable[[], Awaitable[Any]]
    ) -> Any:
        entry = self._entries.get(key)
        if entry is not None:
            age = time.monotonic() - entry[0]
            if age < self.ttl:
                return entry[1]
            if age < self.max_stale:
                self._start(key, load)
                return entry[1]
        return await asyncio.shield(self._start(key, load))

    def clear(self) -> None:
        self._entries.clear()
        self._loads.clear()

    def _start(
        self, key: Hashable, load: Callable[[], Awaitable[Any]]
    ) -> asyncio.Future:
        running = self._loads.get(key)
        if running is not None:
            return running
        task = asyncio.ensure_future(self._load(key, load))
        self._loads[key] = task
        task.add_done_callback(lambda done: self._finished(key, done))
        return task

    async def _load(
        self, key: Hashable, load: Callable[[], Awaitable[Any]]
    ) -> Any:
        value = await load()
        self._entries[key] = (time.monotonic(), value)
        if len(self._entries) > self.max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k][0])
            self._entries.pop(oldest, None)
        return value

    def _finished(self, key: Hashable, task: asyncio.Future) -> None:
        if self._loads.get(key) is task:
            self._loads.pop(key, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and key in self._entries:
            # A stale value was served: the caller never sees this failure.
            logger.warning(
                "%s: refresh of %r failed: %s", self.name, key, error
            )


def clear_all() -> None:
    for cache in list(_instances):
        cache.clear()
