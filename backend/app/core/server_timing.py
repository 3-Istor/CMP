"""
``Server-Timing`` for the routes worth watching: each phase awaited through
:func:`timed` shows up in the browser's network panel and in curl -v.

The phases live in a context variable, so the tasks of an
``asyncio.gather`` add to the list of the request that started them.
"""

import time
from contextvars import ContextVar
from typing import Awaitable, TypeVar

T = TypeVar("T")

_phases: ContextVar[list[tuple[str, float]] | None] = ContextVar(
    "server_timing", default=None
)


def start() -> float:
    _phases.set([])
    return time.perf_counter()


async def timed(name: str, awaitable: Awaitable[T]) -> T:
    began = time.perf_counter()
    try:
        return await awaitable
    finally:
        phases = _phases.get()
        if phases is not None:
            phases.append((name, (time.perf_counter() - began) * 1000))


def lap(name: str, since: float) -> None:
    """A phase that ran before a :func:`timed` could wrap it (auth)."""
    phases = _phases.get()
    if phases is not None:
        phases.append((name, (time.perf_counter() - since) * 1000))


def header(started: float) -> str:
    total = (time.perf_counter() - started) * 1000
    parts = [f"{name};dur={ms:.1f}" for name, ms in _phases.get() or []]
    return ", ".join([*parts, f"total;dur={total:.1f}"])
