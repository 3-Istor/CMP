"""
Which projects were on a cloud, hour by hour, rebuilt from the projects and
their moves. Used to split a cluster's node cost when OpenCost has no data.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from app.services.finops.ingest.projects import IDLE

# (project, cloud, start, end); end None = still there.
Stay = tuple[str, str, datetime, datetime | None]


def _utc(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; they are stored as UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def stays(
    projects: list[tuple[str, str, datetime]],
    moves: list[tuple[str, str, str, str, datetime, datetime | None]],
) -> list[Stay]:
    """A done move keeps the project on both clouds while it runs."""
    done: dict[str, list] = defaultdict(list)
    for project, source, target, status, created, finished in moves:
        if status == "done":
            done[project].append(
                (_utc(created), _utc(finished or created), source, target)
            )
    result: list[Stay] = []
    for project, cloud, created in projects:
        start = _utc(created)
        history = sorted(done[project])
        if not history:
            result.append((project, cloud, start, None))
            continue
        cloud = history[0][2]
        for move_start, move_end, _source, target in history:
            result.append((project, cloud, start, move_end))
            cloud, start = target, move_start
        result.append((project, cloud, start, None))
    return result


def hours_present(
    stays: list[Stay], cloud: str, day: date
) -> dict[str, set[int]]:
    """UTC hours of ``day`` overlapping each project's stays on ``cloud``."""
    midnight = datetime.combine(day, time.min, tzinfo=timezone.utc)
    present: dict[str, set[int]] = defaultdict(set)
    for project, where, start, end in stays:
        if where != cloud:
            continue
        for hour in range(24):
            begin = midnight + timedelta(hours=hour)
            if start < begin + timedelta(hours=1) and (
                end is None or end > begin
            ):
                present[project].add(hour)
    return dict(present)


def shares(
    total: float,
    cluster_hours: int | None,
    present: dict[str, set[int]],
    apps: dict[str, list[tuple[str | None, str]]],
) -> dict[tuple[str, str | None, str], float]:
    """Each hour's cost goes evenly to the projects there, then their apps."""
    hours = set().union(*present.values())
    slots = max(cluster_hours or 0, len(hours))
    if not slots:
        return {(IDLE, None, IDLE): total}
    hourly = total / slots
    costs: dict[tuple[str, str | None, str], float] = defaultdict(float)
    for hour in hours:
        here = [project for project, seen in present.items() if hour in seen]
        for project in here:
            for app, namespace in apps[project]:
                costs[(project, app, namespace)] += (
                    hourly / len(here) / len(apps[project])
                )
    if slots > len(hours):
        costs[(IDLE, None, IDLE)] = hourly * (slots - len(hours))
    *head, last = sorted(costs, key=lambda key: key[2])
    costs[last] = total - sum(costs[key] for key in head)
    return dict(costs)
