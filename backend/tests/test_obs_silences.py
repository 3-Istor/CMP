import asyncio
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from app.services.observability import silences

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def _build(matchers=None, hours=1.0):
    return silences.build(
        "shop", matchers or {}, timedelta(hours=hours), "alice", "", NOW
    )


def test_the_project_matcher_is_always_first_and_exact():
    body = _build({"cnp_app": "web"})

    assert body["matchers"][0] == {
        "name": "cnp_project",
        "value": "shop",
        "isRegex": False,
        "isEqual": True,
    }


def test_a_client_cannot_set_the_project_matcher_itself():
    with pytest.raises(ValueError):
        _build({"cnp_project": "other"})


def test_labels_outside_the_list_are_refused():
    with pytest.raises(ValueError):
        _build({"namespace": "other-web"})


def test_no_user_matcher_is_ever_a_regex():
    body = _build({"alertname": ".*", "severity": "critical"})

    assert not any(m["isRegex"] for m in body["matchers"])


def test_a_silence_cannot_outlast_the_maximum():
    with pytest.raises(ValueError):
        _build(hours=24 * 30)


@pytest.mark.parametrize(
    "matcher",
    [
        {"name": "cnp_project", "value": "shop.*", "isRegex": True},
        {"name": "cnp_project", "value": "shop", "isEqual": False},
        {"name": "cnp_project", "value": "shopping"},
        {"name": "cnp_app", "value": "shop"},
    ],
)
def test_only_an_exact_positive_project_matcher_belongs(matcher):
    assert not silences.belongs({"matchers": [matcher]}, "shop")


def _response(status: int, body) -> mock.Mock:
    return mock.Mock(status_code=status, json=mock.Mock(return_value=body))


def test_another_projects_silence_cannot_be_expired():
    other = {"matchers": [{"name": "cnp_project", "value": "other"}]}
    call = mock.AsyncMock(return_value=_response(200, other))

    with mock.patch.object(silences, "_call", call):
        with pytest.raises(silences.SilenceNotFoundError):
            asyncio.run(silences.expire("shop", "id-1"))

    assert [c.args[0] for c in call.call_args_list] == ["GET"]


def test_listing_drops_silences_of_other_projects_and_expired_ones():
    found = [
        {"id": "a", "matchers": [{"name": "cnp_project", "value": "shop"}]},
        {
            "id": "b",
            "matchers": [
                {"name": "cnp_project", "value": "sho.*", "isRegex": True}
            ],
        },
        {
            "id": "c",
            "matchers": [{"name": "cnp_project", "value": "shop"}],
            "status": {"state": "expired"},
        },
    ]
    call = mock.AsyncMock(return_value=_response(200, found))

    with mock.patch.object(silences, "_call", call):
        listed = asyncio.run(silences.list_for("shop"))

    assert [s["id"] for s in listed] == ["a"]
