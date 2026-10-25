import pytest

from app.models.cluster import Cluster, ClusterState
from app.models.project import TargetCloud
from app.services.clusters import state
from tests.test_app_security_data import session_factory  # noqa: F401

AWS = TargetCloud.AWS


def _force(factory, st):
    with factory() as db:
        db.merge(Cluster(cloud=AWS, state=st))
        db.commit()


def test_up_allowed_from_down_and_error(session_factory):
    with session_factory() as db:  # no row == down
        row = state.begin(db, AWS, "up", "alice")
        assert row.state == ClusterState.STARTING and row.attempts == 1
        assert row.log_path.startswith("data/cluster_ops/aws-")
    _force(session_factory, ClusterState.ERROR)
    with session_factory() as db:
        assert state.begin(db, AWS, "up", "alice").state == ClusterState.STARTING


@pytest.mark.parametrize("st", [ClusterState.STARTING, ClusterState.UP, ClusterState.STOPPING])
def test_up_refused_while_starting_or_up(session_factory, st):
    _force(session_factory, st)
    with session_factory() as db, pytest.raises(state.TransitionError):
        state.begin(db, AWS, "up", "alice")


def test_down_allowed_from_up_and_error(session_factory):
    for st in (ClusterState.UP, ClusterState.ERROR):
        _force(session_factory, st)
        with session_factory() as db:
            assert state.begin(db, AWS, "down", "bob").state == ClusterState.STOPPING
    _force(session_factory, ClusterState.DOWN)
    with session_factory() as db, pytest.raises(state.TransitionError):
        state.begin(db, AWS, "down", "bob")


def test_finish_ok_maps_operation_to_stable_state(session_factory):
    with session_factory() as db:
        state.begin(db, AWS, "up", "a")
        state.finish(db, AWS, ok=True)
        assert db.get(Cluster, AWS).state == ClusterState.UP
        state.begin(db, AWS, "down", "a")
        state.set_step(db, AWS, "destroy", 1, 3)
        assert db.get(Cluster, AWS).total == 3
        state.finish(db, AWS, ok=True)
        assert db.get(Cluster, AWS).state == ClusterState.DOWN


def test_finish_failure_sets_error_and_message(session_factory):
    with session_factory() as db:
        state.begin(db, AWS, "up", "a")
        state.finish(db, AWS, ok=False, error="boom")
        row = db.get(Cluster, AWS)
        assert row.state == ClusterState.ERROR and row.last_error == "boom"
