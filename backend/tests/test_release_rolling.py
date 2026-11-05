from datetime import datetime, timezone

import pytest

from app.models.release import ReleaseEvent
from app.services.release.rolling import (
    RollingRevision,
    revisions_of,
    sync_rolling,
)
from tests.test_release_api import session_factory  # noqa: F401

STARTED = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)


def _rs(revision, image="ghcr.io/o/web:1", owner="Deployment", replicas=1):
    annotations = {"deployment.kubernetes.io/revision": str(revision)}
    return {
        "metadata": {
            "name": f"web-{revision}",
            "creationTimestamp": "2026-11-05T09:00:00Z",
            "annotations": annotations if revision else {},
            "ownerReferences": [{"kind": owner, "name": "web"}],
        },
        "spec": {
            "replicas": replicas,
            "template": {
                "spec": {
                    "containers": [
                        {"name": "sidecar", "image": "other:1"},
                        {"name": "cnp-generic-app", "image": image},
                    ]
                }
            },
        },
    }


def _deployment(reason):
    condition = {"type": "Progressing", "status": "True", "reason": reason}
    return {"status": {"conditions": [condition]}}


def _revision(revision, outcome="deployed"):
    return RollingRevision("prod", "app", revision, "img", STARTED, outcome)


def _rows(factory):
    with factory() as db:
        return {
            r.revision: r
            for r in db.query(ReleaseEvent).filter_by(strategy="rolling")
        }


def test_each_replica_set_of_a_deployment_is_a_revision():
    # Act
    found = revisions_of(
        "staging",
        "backend",
        [_rs(1, "ghcr.io/o/web:1"), _rs(2, "ghcr.io/o/web:2")],
        _deployment("NewReplicaSetAvailable"),
    )

    # Assert
    assert [
        (r.environment, r.component, r.revision, r.image, r.started_at)
        for r in found
    ] == [
        ("staging", "backend", 1, "ghcr.io/o/web:1", STARTED),
        ("staging", "backend", 2, "ghcr.io/o/web:2", STARTED),
    ]
    assert [r.outcome for r in found] == ["deployed", "deployed"]


def test_replica_sets_of_a_rollout_are_left_to_the_canary_history():
    assert revisions_of("prod", "app", [_rs(1, owner="Rollout")], None) == []


def test_a_replica_set_without_revision_is_ignored():
    assert revisions_of("prod", "app", [_rs(0)], None) == []


@pytest.mark.parametrize(
    "reason, outcome",
    [
        ("NewReplicaSetAvailable", "deployed"),
        ("ProgressDeadlineExceeded", "failed"),
        ("ReplicaSetUpdated", "running"),
    ],
)
def test_the_newest_revision_reads_its_outcome_from_the_conditions(
    reason, outcome
):
    # Act
    found = revisions_of("prod", "app", [_rs(1), _rs(2)], _deployment(reason))

    # Assert
    assert [r.outcome for r in found] == ["deployed", outcome]


def test_two_passes_do_not_duplicate_rows(session_factory):
    # Act
    with session_factory() as db:
        first = sync_rolling(db, 7, [_revision(1), _revision(2)])
        second = sync_rolling(db, 7, [_revision(1), _revision(2)])

    # Assert
    rows = _rows(session_factory)
    assert (first, second, len(rows)) == (2, 0, 2)
    row = rows[2]
    assert (row.strategy, row.actors, row.component, row.environment) == (
        "rolling",
        [],
        "app",
        "prod",
    )
    assert row.finished_at is not None


def test_scaling_to_zero_adds_no_row(session_factory):
    # Arrange
    def read(replicas):
        return revisions_of(
            "prod",
            "app",
            [_rs(1, replicas=replicas)],
            _deployment("NewReplicaSetAvailable"),
        )

    # Act
    with session_factory() as db:
        sync_rolling(db, 7, read(2))
        changed = sync_rolling(db, 7, read(0))

    # Assert
    assert (changed, len(_rows(session_factory))) == (0, 1)


def test_a_running_row_is_closed_when_a_newer_revision_appears(
    session_factory,
):
    # Arrange
    with session_factory() as db:
        sync_rolling(db, 7, [_revision(1, "running")])
    assert _rows(session_factory)[1].finished_at is None

    # Act
    with session_factory() as db:
        # The older ReplicaSet is already gone from the cluster.
        changed = sync_rolling(db, 7, [_revision(2, "running")])

    # Assert
    rows = _rows(session_factory)
    assert changed == 2
    assert (rows[1].outcome, rows[1].finished_at is not None) == (
        "superseded",
        True,
    )
    assert (rows[2].outcome, rows[2].finished_at) == ("running", None)

    # Act: the newest one settles
    with session_factory() as db:
        changed = sync_rolling(db, 7, [_revision(2, "failed")])

    # Assert
    assert (changed, _rows(session_factory)[2].outcome) == (1, "failed")
