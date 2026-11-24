from datetime import datetime, timezone

import pytest

from app.services.finops import engine

NOW = datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc)


def _points(*dates):
    return [{"date": d, "total": 1.0} for d in dates]


def test_only_today_is_flagged_partial_with_the_elapsed_part_of_the_utc_day():
    # Act
    points = engine.mark_partial(
        _points("2026-11-23", "2026-11-24"), "daily", NOW
    )

    # Assert
    assert [(p["is_partial"], p["elapsed_fraction"]) for p in points] == [
        (False, 1.0),
        (True, 0.25),
    ]


@pytest.mark.parametrize(
    "granularity, bucket, fraction",
    [
        # Monday 2026-11-23 00:00 to Tuesday 06:00 is 30 h of 168 h.
        ("weekly", "2026-11-23", round(30 / 168, 4)),
        # 23 days and 6 hours of a 30-day month.
        ("monthly", "2026-11-01", round((23 * 24 + 6) / (30 * 24), 4)),
    ],
)
def test_the_current_week_or_month_bucket_is_partial(
    granularity, bucket, fraction
):
    # Act
    (point,) = engine.mark_partial(_points(bucket), granularity, NOW)

    # Assert
    assert (point["is_partial"], point["elapsed_fraction"]) == (True, fraction)
