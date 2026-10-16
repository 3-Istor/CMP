from unittest import mock

import pytest

from app.services.release import route_reconciler


def _application(route_status="OutOfSync", **extra):
    return {
        "metadata": {"name": "shop-web-app"},
        "status": {
            "resources": [
                {"kind": "Service", "status": "Synced"},
                {
                    "kind": "HTTPRoute",
                    "namespace": "shop-web",
                    "name": "web-route",
                    "status": route_status,
                },
            ]
        },
        **extra,
    }


@pytest.fixture(autouse=True)
def fresh_state():
    route_reconciler._stuck_once.clear()
    route_reconciler._resynced.clear()


CANARY_ROUTE = {
    "spec": {
        "rules": [
            {"backendRefs": [{"name": "web-canary", "weight": 1}]},
            {"backendRefs": [{"name": "web-stable", "weight": 1}]},
            {
                "backendRefs": [
                    {"name": "web-stable", "weight": 90},
                    {"name": "web-canary", "weight": 10},
                ]
            },
        ]
    }
}
ROLLING_ROUTE = {
    "spec": {"rules": [{"backendRefs": [{"name": "web", "weight": 1}]}]}
}


def _run(applications, canary_moving=False, passes=2, route=None):
    with (
        mock.patch.object(
            route_reconciler, "kube_list", return_value=applications
        ),
        mock.patch.object(
            route_reconciler, "kube_get", return_value=route or CANARY_ROUTE
        ),
        mock.patch.object(route_reconciler, "kube_merge_patch") as patch,
    ):
        synced = []
        for _ in range(passes):
            synced = route_reconciler.reconcile_route(
                "shop", "web", canary_moving
            )
    return synced, patch


def test_resyncs_a_route_stuck_out_of_sync_for_two_passes():
    # Arrange
    applications = [_application()]

    # Act
    synced, patch = _run(applications)

    # Assert
    assert synced == ["shop-web-app"]
    assert patch.call_args.args[1]["operation"]["sync"]["syncOptions"] == [
        "ServerSideApply=true"
    ]


def test_waits_one_pass_before_resyncing():
    # Act
    synced, patch = _run([_application()], passes=1)

    # Assert
    patch.assert_not_called()


def test_never_resyncs_while_a_canary_is_moving():
    # Act
    synced, patch = _run([_application()], canary_moving=True)

    # Assert
    patch.assert_not_called()


def test_leaves_an_app_with_a_sync_already_running():
    # Act
    synced, patch = _run([_application(operation={"sync": {}})])

    # Assert
    patch.assert_not_called()


def test_leaves_a_synced_route_alone():
    # Act
    synced, patch = _run([_application(route_status="Synced")])

    # Assert
    patch.assert_not_called()


def test_resyncs_only_once_while_the_route_stays_out_of_sync():
    # Act
    _, patch = _run([_application()], passes=8)

    # Assert
    assert patch.call_count == 1


def test_resyncs_again_after_the_route_was_seen_synced():
    # Arrange
    _run([_application()])
    _run([_application("Synced")], passes=1)

    # Act
    synced, _ = _run([_application()])

    # Assert
    assert synced == ["shop-web-app"]


def test_resyncs_a_route_without_canary_backend_while_the_canary_waits_on_it():
    # The Rollout sits at step 0 ("backendRef was not found in httpRoute").
    synced, _ = _run(
        [_application("Synced")], canary_moving=True, route=ROLLING_ROUTE
    )

    assert synced == ["shop-web-app"]


def test_keeps_the_weights_of_a_canary_route_while_the_canary_moves():
    _, patch = _run([_application()], canary_moving=True, route=CANARY_ROUTE)

    patch.assert_not_called()
