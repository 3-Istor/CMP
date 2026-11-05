from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.models.release import ReleaseEvent
from app.services.release import rolling
from app.services.release.rolling import (
    RollingRevision,
    read_rolling,
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


def _revision(revision, outcome="deployed", started_at=None, **kw):
    return RollingRevision(
        kw.get("environment", "prod"),
        "app",
        revision,
        "img",
        started_at or datetime.now(timezone.utc),
        outcome,
    )


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


def test_a_failed_rollout_that_recovers_becomes_deployed(session_factory):
    # Arrange: revision 1 failed long ago, revision 2 has just failed too
    with session_factory() as db:
        sync_rolling(db, 7, [_revision(1, "failed"), _revision(2, "failed")])

    # Act: the pods of revision 2 come up late
    with session_factory() as db:
        changed = sync_rolling(db, 7, [_revision(1), _revision(2)])

    # Assert: only the newest revision tells, the older one stays failed
    rows = _rows(session_factory)
    assert changed == 1
    assert (rows[2].outcome, rows[1].outcome) == ("deployed", "failed")
    assert rows[2].finished_at is not None


def test_a_reused_replica_set_is_dated_when_seen():
    # Arrange: a rollback to an old template gives it a new revision
    reused = _rs(3)
    reused["metadata"]["creationTimestamp"] = "2020-01-01T00:00:00Z"
    reused["metadata"]["annotations"][
        "deployment.kubernetes.io/revision-history"
    ] = "1"

    # Act
    (found,) = revisions_of("prod", "app", [reused], None)

    # Assert
    seen = datetime.now(timezone.utc) - found.started_at
    assert timedelta(0) <= seen < timedelta(minutes=1)


def test_a_deployment_not_yet_observed_reads_running():
    # Arrange: the status still describes the previous generation
    stale = {
        "metadata": {"generation": 4},
        "status": {
            "observedGeneration": 3,
            "conditions": [
                {"type": "Progressing", "reason": "NewReplicaSetAvailable"}
            ],
        },
    }

    # Act
    found = revisions_of("prod", "app", [_rs(1), _rs(2)], stale)

    # Assert
    assert [r.outcome for r in found] == ["deployed", "running"]


def test_read_rolling_reads_each_environment_of_the_app():
    # Arrange
    app = Deployment(id=7, name="web", project_id="shop")
    asked = []

    def fake_list(path, params=None, cloud=None):
        asked.append(("list", path, params, cloud))
        return [] if path.endswith("/rollouts") else [_rs(1)]

    def fake_get(path, params=None, cloud=None):
        asked.append(("get", path, params, cloud))
        if "-stg/" in path:
            return None
        return _deployment("NewReplicaSetAvailable")

    # Act
    with (
        mock.patch.object(rolling, "kube_list", fake_list),
        mock.patch.object(rolling, "kube_get", fake_get),
    ):
        found = read_rolling(app, TargetCloud.AWS, ["prod", "staging"])

    # Assert
    chart = "shop-web-app-cnp-generic-app"
    rollouts = "/apis/argoproj.io/v1alpha1/namespaces/shop-web/rollouts"
    assert asked == [
        ("list", rollouts, None, TargetCloud.AWS),
        (
            "list",
            "/apis/apps/v1/namespaces/shop-web/replicasets",
            {"labelSelector": "app.kubernetes.io/instance=shop-web-app"},
            TargetCloud.AWS,
        ),
        (
            "get",
            f"/apis/apps/v1/namespaces/shop-web/deployments/{chart}",
            None,
            TargetCloud.AWS,
        ),
        (
            "list",
            "/apis/apps/v1/namespaces/shop-web-stg/replicasets",
            {"labelSelector": "app.kubernetes.io/instance=shop-web-app-stg"},
            TargetCloud.AWS,
        ),
        (
            "get",
            f"/apis/apps/v1/namespaces/shop-web-stg/deployments/{chart}",
            None,
            TargetCloud.AWS,
        ),
    ]
    # A missing Deployment (404) leaves the newest revision running.
    assert [(r.environment, r.component, r.outcome) for r in found] == [
        ("prod", "app", "deployed"),
        ("staging", "app", "running"),
    ]


def _read_fullstack_canary(rollouts):
    app = Deployment(
        id=7,
        name="web",
        project_id="shop",
        app_config='{"app_type": "fullstack"}',
    )
    read = []

    def fake_list(path, params=None, cloud=None):
        if path.endswith("/rollouts"):
            return rollouts
        read.append(path)
        return [_rs(1)]

    with (
        mock.patch.object(rolling, "kube_list", fake_list),
        mock.patch.object(rolling, "kube_get", lambda *a, **k: None),
    ):
        found = read_rolling(app, TargetCloud.AWS, ["prod", "staging"])
    return found, read


def test_a_deployment_a_rollout_references_is_not_read():
    # Arrange: the chart keeps the frontend's Deployment as workloadRef
    frontend = "shop-web-frontend-cnp-generic-app"
    rollouts = [{"spec": {"workloadRef": {"name": frontend}}}]

    # Act
    found, _ = _read_fullstack_canary(rollouts)

    # Assert: the unreferenced backend and both staging components still roll
    assert [(r.environment, r.component) for r in found] == [
        ("prod", "backend"),
        ("staging", "frontend"),
        ("staging", "backend"),
    ]


def test_staging_does_not_look_for_rollouts():
    # Arrange: a Rollout in prod must not hide the staging Deployments
    rollouts = [
        {"spec": {"workloadRef": {"name": "shop-web-backend-cnp-generic-app"}}}
    ]
    asked = []

    def fake_list(path, params=None, cloud=None):
        asked.append(path)
        return rollouts if path.endswith("/rollouts") else [_rs(1)]

    app = Deployment(id=7, name="web", project_id="shop")

    # Act
    with (
        mock.patch.object(rolling, "kube_list", fake_list),
        mock.patch.object(rolling, "kube_get", lambda *a, **k: None),
    ):
        read_rolling(app, TargetCloud.AWS, ["staging"])

    # Assert
    assert not any(path.endswith("/rollouts") for path in asked)


def test_the_backfill_inserts_rows_oldest_first(session_factory):
    # Arrange: two environments, the newer environment listed first
    now = datetime.now(timezone.utc)
    revisions = [
        _revision(
            1, environment="staging", started_at=now - timedelta(seconds=5)
        ),
        _revision(
            1, environment="prod", started_at=now - timedelta(seconds=20)
        ),
    ]

    # Act
    with session_factory() as db:
        sync_rolling(db, 7, revisions)

    # Assert: ids follow started_at, so the id-desc listing is chronological
    with session_factory() as db:
        rows = db.query(ReleaseEvent).order_by(ReleaseEvent.id).all()
    assert [r.environment for r in rows] == ["prod", "staging"]


def test_a_backfilled_row_with_an_old_start_has_no_end(session_factory):
    # Arrange
    now = datetime.now(timezone.utc)
    revisions = [
        _revision(1, started_at=now - timedelta(days=3)),
        _revision(2, started_at=now - timedelta(seconds=10)),
        _revision(3, "running", started_at=now - timedelta(days=1)),
    ]

    # Act
    with session_factory() as db:
        sync_rolling(db, 7, revisions)

    # Assert: the end of an old one is unknown, a fresh one just ended
    rows = _rows(session_factory)
    assert [rows[n].finished_at is None for n in (1, 2, 3)] == [
        True,
        False,
        True,
    ]
