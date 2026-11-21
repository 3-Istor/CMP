"""
Logs the event loop's stack when the loop stops answering.

Every request shares one loop, so a sync call made inside an ``async def``
freezes the whole API. A daemon thread pings the loop and, when a ping waits
longer than ``STALL_SECONDS``, logs what the loop thread is running.
"""

import asyncio
import logging
import sys
import threading
import time
import traceback

logger = logging.getLogger(__name__)

PING_SECONDS = 0.5
STALL_SECONDS = 1.0


def _stack(thread_id: int) -> str:
    frame = sys._current_frames().get(
        thread_id
    )  # pylint: disable=protected-access
    if frame is None:
        return "(no frame)"
    return "".join(traceback.format_stack(frame, limit=25))


def _watch(loop: asyncio.AbstractEventLoop, loop_thread: int) -> None:
    while not loop.is_closed():
        answered = threading.Event()
        sent = time.monotonic()
        try:
            loop.call_soon_threadsafe(answered.set)
        except RuntimeError:
            return
        if not answered.wait(STALL_SECONDS):
            logger.warning(
                "Event loop blocked for more than %.1fs, running:\n%s",
                STALL_SECONDS,
                _stack(loop_thread),
            )
            answered.wait()
            logger.warning(
                "Event loop answered again after %.1fs",
                time.monotonic() - sent,
            )
        time.sleep(PING_SECONDS)


def start() -> None:
    """Watch the running loop from a daemon thread."""
    threading.Thread(
        target=_watch,
        args=(asyncio.get_running_loop(), threading.get_ident()),
        name="loop-watchdog",
        daemon=True,
    ).start()
