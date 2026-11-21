import asyncio
import logging
import time

from app.core import loop_watchdog


def test_logs_the_blocking_call_when_the_loop_stalls(monkeypatch, caplog):
    # Arrange
    monkeypatch.setattr(loop_watchdog, "STALL_SECONDS", 0.2)
    monkeypatch.setattr(loop_watchdog, "PING_SECONDS", 0.05)

    def blocking_sync_call():
        time.sleep(0.6)

    async def handler():
        loop_watchdog.start()
        await asyncio.sleep(0.1)
        blocking_sync_call()
        await asyncio.sleep(0.1)

    # Act
    with caplog.at_level(logging.WARNING, logger=loop_watchdog.__name__):
        asyncio.run(handler())

    # Assert
    assert any("blocking_sync_call" in r.getMessage() for r in caplog.records)


def test_stays_quiet_while_the_loop_answers(monkeypatch, caplog):
    # Arrange
    monkeypatch.setattr(loop_watchdog, "STALL_SECONDS", 0.2)
    monkeypatch.setattr(loop_watchdog, "PING_SECONDS", 0.05)

    names = []

    async def handler():
        loop_watchdog.start()
        names.append(loop_watchdog.thread_name(asyncio.get_running_loop()))
        await asyncio.sleep(0.5)

    # Act
    with caplog.at_level(logging.WARNING, logger=loop_watchdog.__name__):
        asyncio.run(handler())

    # Assert: watchdogs of apps started by other tests may log meanwhile
    assert not [r for r in caplog.records if r.threadName == names[0]]
