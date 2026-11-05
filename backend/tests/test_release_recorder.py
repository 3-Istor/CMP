from datetime import datetime, timezone
from unittest import mock

import pytest

from app.models.environment import AppEnvironment
from app.models.project import TargetCloud
from app.models.release import ReleaseEvent
from app.services.kube_client import KubeUnavailableError
from app.services.release import recorder
from app.services.release.history import latest_event
from app.services.release.rolling import RollingRevision
from tests.test_release_api import (  # noqa: F401
    FakeSource,
    _paused,
    session_factory,
)


@pytest.fixture(autouse=True)
def read_rolling():
    # Nothing to read outside a cluster.
    with mock.patch.object(
        recorder, "read_rolling", return_value=[]
    ) as patched:
        yield patched


def test_record_all_records_a_release_nobody_is_watching(session_factory):
    # Arrange
    source = FakeSource(_paused())

    # Act
    with (
        mock.patch.object(recorder, "SessionLocal", session_factory),
        mock.patch.object(recorder, "reconcile_route") as reconcile,
    ):
        recorded = recorder.record_all(source)

    # Assert
    with session_factory() as db:
        assert recorded == 1
        assert db.query(ReleaseEvent).filter_by(deployment_id=7).count() == 1
    reconcile.assert_called_once_with(
        "shop", "web", canary_moving=True, cloud=TargetCloud.ONPREM
    )


def test_record_all_stops_quietly_when_the_cluster_is_unreachable(
    session_factory,
):
    # Arrange
    source = FakeSource(None)
    source.fetch = mock.Mock(side_effect=KubeUnavailableError("down"))

    # Act
    with (
        mock.patch.object(recorder, "SessionLocal", session_factory),
        mock.patch.object(recorder, "reconcile_route") as reconcile,
    ):
        recorded = recorder.record_all(source)

    # Assert
    assert recorded == 0


def test_record_all_keeps_recording_when_the_route_reconcile_fails(
    session_factory,
):
    # Arrange
    source = FakeSource(_paused())

    # Act
    with (
        mock.patch.object(recorder, "SessionLocal", session_factory),
        mock.patch.object(
            recorder,
            "reconcile_route",
            side_effect=KubeUnavailableError("argocd"),
        ),
    ):
        recorded = recorder.record_all(source)

    # Assert
    assert recorded == 1


def test_a_rolling_row_does_not_disturb_the_canary_history(session_factory):
    # Arrange
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        for revision, strategy in ((1, "canary"), (2, "rolling")):
            db.add(
                ReleaseEvent(
                    deployment_id=7,
                    revision=revision,
                    strategy=strategy,
                    started_at=now,
                )
            )
        db.commit()

        # Act
        latest = latest_event(db, 7)

    # Assert
    assert latest.strategy == "canary"
    assert latest.environment == "prod"
    assert latest.component is None


def _record_a_rolling_app(session_factory, read_rolling, staging=None):
    started = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
    read_rolling.return_value = [
        RollingRevision(
            "prod", "app", 3, "ghcr.io/o/web:3", started, "deployed"
        )
    ]
    if staging:
        with session_factory() as db:
            db.add(
                AppEnvironment(
                    deployment_id=7,
                    name="staging",
                    state=staging,
                    updated_at=started,
                )
            )
            db.commit()
    with (
        mock.patch.object(recorder, "SessionLocal", session_factory),
        mock.patch.object(recorder, "reconcile_route"),
    ):
        recorder.record_all(FakeSource(None))


def test_record_all_records_rolling_apps(session_factory, read_rolling):
    # Act
    _record_a_rolling_app(session_factory, read_rolling)

    # Assert
    with session_factory() as db:
        (event,) = db.query(ReleaseEvent).all()
    assert (event.strategy, event.revision, event.component) == (
        "rolling",
        3,
        "app",
    )
    deployment, cloud, environments = read_rolling.call_args.args
    assert (deployment.id, cloud, environments) == (
        7,
        TargetCloud.ONPREM,
        ["prod"],
    )


def test_record_all_reads_staging_once_it_is_ready(
    session_factory, read_rolling
):
    # Act
    _record_a_rolling_app(session_factory, read_rolling, staging="ready")

    # Assert
    assert read_rolling.call_args.args[2] == ["prod", "staging"]


def test_record_all_ignores_a_staging_still_provisioning(
    session_factory, read_rolling
):
    # Act
    _record_a_rolling_app(
        session_factory, read_rolling, staging="provisioning"
    )

    # Assert
    assert read_rolling.call_args.args[2] == ["prod"]
