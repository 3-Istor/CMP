import asyncio
import time

from app.services import monitoring_service as ms


def _hv(name):
    return ms.HypervisorStatus(
        name=name, state="up", status="enabled", ip=None
    )


def _names(health):
    return [h.name for h in health.openstack_hypervisors]


def _fake_listing(monkeypatch, answers, calls):
    async def fake():
        calls.append(1)
        return answers.pop(0)

    monkeypatch.setattr(ms, "_get_openstack_hypervisors", fake)


def test_first_call_waits_for_the_listing(monkeypatch):
    # Arrange
    calls = []
    monkeypatch.setattr(ms, "_hypervisors_cache", None)
    monkeypatch.setattr(ms, "_hypervisors_refresh", None)
    _fake_listing(monkeypatch, [[_hv("node-1")]], calls)

    # Act
    health = asyncio.run(ms.get_global_health())

    # Assert
    assert _names(health) == ["node-1"]


def test_stale_listing_is_served_while_it_refreshes(monkeypatch):
    # Arrange
    calls = []
    old = time.monotonic() - ms.HYPERVISORS_TTL - 1
    monkeypatch.setattr(ms, "_hypervisors_cache", (old, [_hv("old")]))
    monkeypatch.setattr(ms, "_hypervisors_refresh", None)
    _fake_listing(monkeypatch, [[_hv("new")]], calls)

    async def twice():
        first = await ms.get_global_health()
        await ms._hypervisors_refresh  # pylint: disable=protected-access
        second = await ms.get_global_health()
        return first, second

    # Act
    first, second = asyncio.run(twice())

    # Assert
    assert (_names(first), _names(second)) == (["old"], ["new"])


def test_fresh_listing_is_not_read_again(monkeypatch):
    # Arrange
    calls = []
    monkeypatch.setattr(
        ms, "_hypervisors_cache", (time.monotonic(), [_hv("x")])
    )
    monkeypatch.setattr(ms, "_hypervisors_refresh", None)
    _fake_listing(monkeypatch, [[_hv("y")]], calls)

    # Act
    asyncio.run(ms.get_global_health())

    # Assert
    assert calls == []
