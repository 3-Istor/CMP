import asyncio
from datetime import datetime, timezone
from unittest import mock

import pytest

from app.services.moves import wal


@pytest.mark.parametrize(
    "release,generation,expected",
    [
        ("alpha-shop-backend", 0, "alpha-shop-backend-db"),
        ("alpha-shop-backend", 2, "alpha-shop-backend-db-r2"),
        # The stem is cut so stem + suffix fits 50 characters, as the chart does.
        ("a" * 60, 0, "a" * 47 + "-db"),
        ("a" * 60, 12, "a" * 43 + "-db-r12"),
    ],
)
def test_server_name_follows_the_chart(release, generation, expected):
    assert wal.server_name(release, generation) == expected


def test_newest_takes_the_latest_object_over_the_pages():
    old = datetime(2026, 11, 23, 19, 0, tzinfo=timezone.utc)
    new = datetime(2026, 11, 23, 19, 58, 2, tzinfo=timezone.utc)
    client = mock.Mock()
    paginator = client.get_paginator.return_value
    paginator.paginate.return_value = [
        {"Contents": [{"LastModified": old}]},
        {},
        {"Contents": [{"LastModified": new}, {"LastModified": old}]},
    ]

    assert wal._newest(client, "bucket", "srv") == new
    paginator.paginate.assert_called_once_with(
        Bucket="bucket", Prefix="srv/wals/"
    )


def test_one_unreadable_bucket_gives_a_reason_not_an_error():
    when = datetime(2026, 11, 23, 19, 58, tzinfo=timezone.utc)

    async def last(_project, app, _values):
        if app == "bad":
            raise RuntimeError("no WAL archived")
        return when

    with (
        mock.patch.object(
            wal,
            "backed_up",
            mock.AsyncMock(return_value=[("shop", {}), ("bad", {})]),
        ),
        mock.patch.object(wal, "_last_wal", last),
    ):
        found = asyncio.run(wal.last_archived(None, "alpha"))

    assert found == [
        {"database": "shop", "last_wal_at": when.isoformat(), "reason": None},
        {"database": "bad", "last_wal_at": None, "reason": "no WAL archived"},
    ]
