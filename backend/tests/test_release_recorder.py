from unittest import mock

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
    with mock.patch.object(recorder, "SessionLocal", session_factory):
        recorded = recorder.record_all(source)

    # Assert
    with session_factory() as db:
        assert recorded == 1
        assert db.query(ReleaseEvent).filter_by(deployment_id=7).count() == 1


def test_record_all_stops_quietly_when_the_cluster_is_unreachable(
    session_factory,
):
    # Arrange
    source = FakeSource(None)
    source.fetch = mock.Mock(side_effect=KubeUnavailableError("down"))

    # Act
    with mock.patch.object(recorder, "SessionLocal", session_factory):
        recorded = recorder.record_all(source)

    # Assert
    assert recorded == 0
