import asyncio
from unittest import mock

import pytest
from fastapi import HTTPException

from app.models.project import TargetCloud
from app.services.clusters import state
from app.services.kube_client import KubeUnavailableError

AWS = TargetCloud.AWS


def _app(health):
    return {"status": {"health": {"status": health}}}


@pytest.mark.parametrize(
    "app,expected",
    [
        (_app("Healthy"), "up"),
        (_app("Progressing"), "starting"),
        (_app("Missing"), "down"),
        (_app("Degraded"), "down"),
        (_app("Unknown"), "down"),
        (_app("Suspended"), "down"),
        (None, "unknown"),  # Application absent
    ],
)
def test_fresh_state_maps_argocd_health(app, expected):
    with mock.patch.object(state, "kube_get", return_value=app) as get:
        assert state.fresh_state(AWS) == expected
    get.assert_called_once_with(
        "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/wireguard-aws"
    )


def test_fresh_state_read_error_is_unknown():
    with mock.patch.object(
        state, "kube_get", side_effect=KubeUnavailableError("boom")
    ):
        assert state.fresh_state(AWS) == "unknown"


def _guard(cloud, *states):
    reads = iter(states)
    with (
        mock.patch.object(state, "fresh_state", lambda c: next(reads)),
        mock.patch.object(state.asyncio, "sleep", mock.AsyncMock()) as sleep,
    ):
        try:
            asyncio.run(state.ensure_cluster_up(cloud))
        except HTTPException as exc:
            return exc, sleep.await_count
    return None, sleep.await_count


def test_guard_passes_when_up():
    assert _guard(AWS, "up") == (None, 0)


@pytest.mark.parametrize(
    "st,code,text",
    [
        ("down", 409, "The AWS cluster is down"),
        ("starting", 409, "The AWS cluster is starting"),
    ],
)
def test_guard_refuses_down_and_starting(st, code, text):
    exc, _ = _guard(AWS, st)
    assert exc.status_code == code and text in exc.detail


def test_guard_waits_for_a_stable_state():
    assert _guard(AWS, "unknown", "unknown", "up") == (None, 2)


def test_guard_gives_up_on_persistent_unknown():
    exc, sleeps = _guard(TargetCloud.GCP, *["unknown"] * 10)
    assert exc.status_code == 503 and "GCP cluster state is unknown" in exc.detail
    assert sleeps == state.GUARD_RETRIES


def test_guard_ignores_onprem():
    assert _guard(TargetCloud.ONPREM) == (None, 0)


def test_health_states_never_wait_on_the_read():
    async def run():
        with mock.patch.object(state, "fresh_state", lambda c: "up"):
            state._cached_at = None
            state._cache = {}
            first = await state.cluster_states()  # returns before the read
            await state._refresh_task
            return first, await state.cluster_states()

    first, second = asyncio.run(run())
    assert first == {AWS: "unknown", TargetCloud.GCP: "unknown"}
    assert second == {AWS: "up", TargetCloud.GCP: "up"}
