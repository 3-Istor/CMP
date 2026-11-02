from unittest import mock

from app.models.project import TargetCloud
from app.models.release import ReleaseEvent
from app.services.kube_client import KubeUnavailableError
from app.services.release import recorder
from tests.test_release_api import (  # noqa: F401
    FakeSource,
    _paused,
    session_factory,
)


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
