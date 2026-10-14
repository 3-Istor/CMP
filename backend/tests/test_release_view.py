from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.release import ReleaseEvent
from app.services.release import history
from app.services.release.source import RolloutSnapshot, pick_rollout
from app.services.release.view import (
    ReleaseHint,
    build_view,
    metric_threshold,
    parse_duration,
    parse_measurement,
    promote_patch,
    skip_patch,
)

NOW = datetime(2026, 10, 14, 14, 10, tzinfo=timezone.utc)
STABLE = "684dcb4995"
CANARY = "79848bfc6c"
UID = "5cbf961a-6ebe-4da8-b4d6-8cd4ab0eb0ec"


def _ts(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error_rate(duration: str) -> dict:
    return {
        "analysis": {
            "templates": [{"templateName": "cnp-error-rate", "clusterScope": True}],
            "args": [
                {"name": "duration", "value": duration},
                {"name": "max-error-rate", "value": "0.01"},
            ],
        }
    }


# The chart's prefix, then a preset.
PREFIX = [
    {"setCanaryScale": {"replicas": 1}},
    {"analysis": {"templates": [{"templateName": "cnp-smoke", "clusterScope": True}]}},
    {"setWeight": 10},
    {"setCanaryScale": {"matchTrafficWeight": True}},
]
SAFE = PREFIX + [
    _error_rate("2m"),
    {"setWeight": 25},
    {"pause": {}},
    {"setWeight": 50},
    _error_rate("2m"),
    {"setWeight": 100},
]
FAST = PREFIX + [_error_rate("1m"), {"setWeight": 100}]


def _rollout(steps=SAFE, annotations=None, **status) -> dict:
    return {
        "metadata": {
            "name": "web",
            "namespace": "release-lab",
            "uid": UID,
            "annotations": {
                "rollout.argoproj.io/revision": "5",
                "cnp.3istor.com/release-preset": "safe",
                **(annotations or {}),
            },
        },
        "spec": {"strategy": {"canary": {"steps": steps}}},
        "status": {
            "stableRS": STABLE,
            "currentPodHash": CANARY,
            "phase": "Progressing",
            **status,
        },
    }


def _weights(canary: int) -> dict:
    return {
        "weights": {
            "canary": {"podTemplateHash": CANARY, "weight": canary},
            "stable": {"podTemplateHash": STABLE, "weight": 100 - canary},
        }
    }


def _run(step: int, phase: str, values=(), started=60, completed=None, name="error-rate") -> dict:
    measurements = [
        {"phase": "Successful" if phase == "Running" else phase, "value": v}
        for v in values
    ]
    status = {
        "phase": phase,
        "startedAt": _ts(started),
        "metricResults": [{"name": name, "phase": phase, "measurements": measurements}],
    }
    if completed is not None:
        status["completedAt"] = _ts(completed)
    if phase == "Failed":
        status["message"] = 'Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)'
    return {
        "metadata": {
            "name": f"web-{CANARY}-5-{step}",
            "creationTimestamp": _ts(started),
            "annotations": {"rollout.argoproj.io/revision": "5"},
            "labels": {
                "rollout-type": "Step",
                "step-index": str(step),
                "rollouts-pod-template-hash": CANARY,
            },
        },
        "spec": {
            "args": [{"name": "canary-hash", "value": CANARY}, {"name": "max-error-rate", "value": "0.01"}],
            "metrics": [
                {
                    "name": name,
                    "count": 4,
                    "interval": "30s",
                    "successCondition": "len(result) > 0 && result[0] <= {{args.max-error-rate}}",
                    "failureCondition": "len(result) > 0 && result[0] > {{args.max-error-rate}}",
                }
            ],
        },
        "status": status,
    }


def _smoke_done() -> dict:
    run = _run(1, "Successful", started=400, completed=392, name="smoke")
    run["spec"]["metrics"][0].pop("successCondition")
    run["spec"]["metrics"][0].pop("failureCondition")
    return run


def _rs(pod_hash: str, image: str, ready: int, desired: int) -> dict:
    return {
        "metadata": {
            "name": f"web-{pod_hash}",
            "creationTimestamp": _ts(500),
            "labels": {"rollouts-pod-template-hash": pod_hash},
        },
        "spec": {"replicas": desired, "template": {"spec": {"containers": [{"image": image}]}}},
        "status": {"readyReplicas": ready},
    }


REPLICA_SETS = [
    _rs(STABLE, "ghcr.io/3-istor/web:1.3.0", 3, 3),
    _rs(CANARY, "ghcr.io/3-istor/web:1.4.0", 1, 1),
]


def _snapshot(rollout, runs=()) -> RolloutSnapshot:
    return RolloutSnapshot(rollout=rollout, analysis_runs=list(runs), replica_sets=REPLICA_SETS)


def _statuses(view) -> list[str]:
    return [s.status for s in view.steps]


class TestPausedAtManualStep:
    @pytest.fixture
    def view(self):
        rollout = _rollout(
            currentStepIndex=6,
            phase="Paused",
            pauseConditions=[{"reason": "CanaryPauseStep", "startTime": _ts(30)}],
            canary=_weights(25),
        )
        runs = [_smoke_done(), _run(4, "Successful", ["[0.0021]"], started=200, completed=80)]
        return build_view(_snapshot(rollout, runs), now=NOW)

    def test_steps_before_the_pause_are_done_and_the_pause_waits(self, view):
        assert _statuses(view) == ["done"] * 6 + ["active"] + ["pending"] * 3

    def test_measured_error_rate_comes_from_the_last_measurement(self, view):
        metric = view.steps[4].metrics[0]

        assert (metric.value, metric.threshold, metric.comparator, metric.phase) == (
            0.0021,
            0.01,
            "<=",
            "Successful",
        )

    def test_reports_the_traffic_split_and_both_revisions(self, view):
        assert (view.weights.stable, view.weights.canary, view.stable_image, view.canary_image) == (
            75,
            25,
            "ghcr.io/3-istor/web:1.3.0",
            "ghcr.io/3-istor/web:1.4.0",
        )

    def test_offers_continue_but_not_skip(self, view):
        assert view.actions.model_dump() == {
            "promote": True,
            "promote_full": True,
            "skip": False,
            "abort": True,
            "retry": False,
        }

    def test_has_no_eta_while_waiting_for_a_human(self, view):
        assert view.eta_seconds is None

    def test_labels_the_chart_prefix_steps_as_scale(self, view):
        assert [(s.kind, s.label) for s in view.steps[:4]] == [
            ("scale", "Préparation du canary"),
            ("analysis", "Smoke test"),
            ("setWeight", "Trafic 10 %"),
            ("scale", "Alignement des pods"),
        ]

    def test_reads_the_preset_from_the_rollout(self, view):
        assert view.preset == "safe"


def test_running_analysis_counts_its_remaining_time_in_the_eta():
    rollout = _rollout(steps=FAST, currentStepIndex=4, canary=_weights(10))
    runs = [_smoke_done(), _run(4, "Running", ["[0.0021]"], started=20)]

    view = build_view(_snapshot(rollout, runs), now=NOW)

    assert (view.steps[4].status, view.eta_seconds, view.actions.skip) == ("running", 40, True)


def test_eta_adds_the_analysis_durations_still_ahead():
    rollout = _rollout(steps=FAST, currentStepIndex=2, canary=_weights(0))

    view = build_view(_snapshot(rollout, [_smoke_done()]), now=NOW)

    assert view.eta_seconds == 60


class TestAutomaticAbort:
    @pytest.fixture
    def view(self):
        rollout = _rollout(
            abort=True,
            currentStepIndex=0,
            phase="Degraded",
            canary=_weights(0),
        )
        runs = [
            _smoke_done(),
            _run(4, "Failed", ["[0.6799999999999999]", "[0.6799999999999999]"], started=40, completed=20),
        ]
        return build_view(_snapshot(rollout, runs), now=NOW)

    def test_marks_the_failed_analysis_although_the_index_was_reset(self, view):
        assert _statuses(view) == ["done"] * 4 + ["failed"] + ["pending"] * 5

    def test_explains_the_failure_with_the_run_message(self, view):
        assert "assessed Failed" in view.steps[4].message

    def test_keeps_the_failing_measurement(self, view):
        assert view.steps[4].metrics[0].value == pytest.approx(0.68)

    def test_only_offers_retry(self, view):
        assert (view.phase, view.actions.retry, view.actions.abort, view.actions.promote_full) == (
            "Aborted",
            True,
            False,
            False,
        )


def test_manual_abort_points_at_the_step_the_cmp_saw_last():
    rollout = _rollout(abort=True, currentStepIndex=0, canary=_weights(0))

    view = build_view(_snapshot(rollout), now=NOW, hint=ReleaseHint(reached_step=6))

    assert view.steps[6].status == "failed"


def test_finished_release_shows_every_step_done_and_full_traffic_on_stable():
    rollout = _rollout(currentPodHash=STABLE, phase="Healthy", currentStepIndex=10)

    view = build_view(_snapshot(rollout), now=NOW)

    assert (set(_statuses(view)), view.canary_hash, view.weights.stable, view.actions.abort) == (
        {"done"},
        None,
        100,
        False,
    )


def test_force_actions_disappear_when_the_chart_forbids_forcing():
    rollout = _rollout(
        steps=FAST,
        annotations={"cnp.3istor.com/release-allow-force": "false"},
        currentStepIndex=4,
    )

    view = build_view(_snapshot(rollout, [_run(4, "Running", started=10)]), now=NOW)

    assert (view.actions.promote_full, view.actions.skip, view.actions.abort) == (False, False, True)


def test_skipped_and_force_promoted_steps_show_as_skipped():
    rollout = _rollout(currentPodHash=STABLE, phase="Healthy")

    view = build_view(_snapshot(rollout), now=NOW, hint=ReleaseHint(skipped={4}, skipped_from=8))

    assert _statuses(view) == ["done"] * 4 + ["skipped"] + ["done"] * 3 + ["skipped"] * 2


def test_no_rollout_gives_a_disabled_view():
    assert build_view(None).enabled is False


def test_prefers_the_rollout_named_after_the_app():
    rollouts = [{"metadata": {"name": n}} for n in ("shop-backend", "shop", "shop-frontend")]

    assert pick_rollout(rollouts, "shop")["metadata"]["name"] == "shop"


@pytest.mark.parametrize(
    "raw, expected",
    [("[0.68]", 0.68), ("0.002", 0.002), ("[]", None), ("[NaN]", None), (None, None), ("garbage", None)],
)
def test_parses_prometheus_measurements(raw, expected):
    assert parse_measurement(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("2m", 120), ("30s", 30), ("1m30s", 90), ("1h", 3600), (10, 10), ("10", 10), ("soon", None), (None, None)],
)
def test_parses_go_durations(raw, expected):
    assert parse_duration(raw) == expected


def test_threshold_from_a_failure_condition_passes_below_it():
    metric = {"failureCondition": "len(result) > 0 && result[0] > 0.05"}

    assert metric_threshold(metric, {}) == (0.05, "<=")


class TestStatusPatches:
    def test_continue_clears_the_pause(self):
        rollout = _rollout(currentStepIndex=6, pauseConditions=[{"reason": "CanaryPauseStep"}])

        assert promote_patch(rollout, "next") == {"status": {"pauseConditions": None}}

    def test_promote_all_sets_promote_full(self):
        assert promote_patch(_rollout(), "full") == {"status": {"promoteFull": True}}

    def test_continue_past_an_inconclusive_analysis_moves_the_index(self):
        rollout = _rollout(
            currentStepIndex=4,
            controllerPause=True,
            pauseConditions=[{"reason": "InconclusiveAnalysisRun"}],
        )

        assert promote_patch(rollout, "next") == {
            "status": {"pauseConditions": None, "controllerPause": False, "currentStepIndex": 5}
        }

    def test_skip_moves_past_the_running_analysis(self):
        assert skip_patch(_rollout(currentStepIndex=4)) == {"status": {"currentStepIndex": 5}}


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _running_view(step=4):
    return build_view(_snapshot(_rollout(currentStepIndex=step), [_smoke_done(), _run(4, "Running", started=10)]), now=NOW)


class TestHistory:
    def test_polling_the_same_state_writes_one_row(self, db):
        for _ in range(3):
            history.sync_event(db, 1, _running_view())

        assert [(e.revision, e.outcome) for e in db.query(ReleaseEvent)] == [(5, "running")]

    def test_records_how_far_the_release_went(self, db):
        history.sync_event(db, 1, _running_view(4))
        history.sync_event(db, 1, _running_view(6))

        assert db.query(ReleaseEvent).one().reached_step == 6

    def test_an_abort_nobody_asked_for_is_automatic(self, db):
        history.sync_event(db, 1, _running_view())
        aborted = build_view(_snapshot(_rollout(abort=True), [_run(4, "Failed", started=30, completed=5)]), now=NOW)

        history.sync_event(db, 1, aborted)

        assert db.query(ReleaseEvent).one().outcome == "aborted_auto"

    def test_an_abort_from_the_cmp_is_manual_even_if_the_poll_saw_it_first(self, db):
        event = history.sync_event(db, 1, _running_view())
        history.sync_event(db, 1, build_view(_snapshot(_rollout(abort=True)), now=NOW))

        history.record_action(db, event, "alice", "abort", None, 4, False)

        assert db.query(ReleaseEvent).one().outcome == "aborted_manual"

    def test_a_finished_release_is_promoted(self, db):
        history.sync_event(db, 1, _running_view())
        done = build_view(_snapshot(_rollout(currentPodHash=STABLE, phase="Healthy")), now=NOW)

        history.sync_event(db, 1, done)

        event = db.query(ReleaseEvent).one()
        assert (event.outcome, event.reached_step) == ("promoted", 10)

    def test_a_newer_revision_supersedes_the_running_one(self, db):
        history.sync_event(db, 1, _running_view())
        newer = _rollout(annotations={"rollout.argoproj.io/revision": "6"}, currentStepIndex=0)

        history.sync_event(db, 1, build_view(_snapshot(newer), now=NOW))

        assert [(e.revision, e.outcome) for e in db.query(ReleaseEvent).order_by(ReleaseEvent.id)] == [
            (5, "superseded"),
            (6, "running"),
        ]

    def test_force_and_skips_feed_back_into_the_view(self, db):
        event = history.sync_event(db, 1, _running_view())
        history.record_action(db, event, "alice", "skip", "metrics down", 4, True)

        hint = history.hint_from(history.latest_event(db, 1), 5)

        assert (hint.forced, hint.skipped) == (True, {4})
