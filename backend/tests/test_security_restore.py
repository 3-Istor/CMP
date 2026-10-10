from datetime import datetime

import pytest

from app.services.security import restore

NOW = datetime(2026, 10, 9, 12, 0)


def _backup(phase="completed", method="plugin", backup_id="20261009T030000"):
    return {
        "metadata": {"name": "api-db-daily-20261009030000"},
        "spec": {"cluster": {"name": "api-db"}, "method": method},
        "status": {
            "phase": phase,
            "backupId": backup_id,
            "stoppedAt": "2026-10-09T03:02:00Z",
        },
    }


def test_restore_targets_the_server_and_backup_id_of_the_backup():
    target = restore.target_from_backup(_backup(), None, NOW)

    assert (target.server, target.backup_id) == ("api-db", "20261009T030000")


@pytest.mark.parametrize(
    "backup",
    [
        _backup(phase="running"),
        _backup(method="volumeSnapshot"),
        _backup(backup_id=""),
    ],
)
def test_restore_refuses_a_backup_that_cannot_be_restored(backup):
    with pytest.raises(restore.RestoreError):
        restore.target_from_backup(backup, None, NOW)


@pytest.mark.parametrize(
    "target_time", [datetime(2026, 10, 9, 3, 0), datetime(2026, 10, 9, 13, 0)]
)
def test_restore_refuses_an_instant_outside_the_replayable_window(target_time):
    with pytest.raises(restore.RestoreError):
        restore.target_from_backup(_backup(), target_time, NOW)


def test_first_restore_creates_generation_one():
    target = restore.target_from_backup(_backup(), None, NOW)

    patch = restore.restore_patch({"db": {"enabled": True}}, target)

    assert patch["db"]["restore"] == {
        "generation": 1,
        "sourceServer": "api-db",
        "backupID": "20261009T030000",
        "targetTime": "",
    }


def test_next_restore_bumps_the_generation_and_writes_the_instant_in_utc():
    target = restore.target_from_backup(
        _backup(), datetime(2026, 10, 9, 10, 30), NOW
    )
    values = {"db": {"restore": {"generation": 2}}}

    restore_values = restore.restore_patch(values, target)["db"]["restore"]

    assert (restore_values["generation"], restore_values["targetTime"]) == (
        3,
        "2026-10-09T10:30:00Z",
    )


def test_database_read_reports_what_a_restored_cluster_came_from():
    cluster = {
        "metadata": {"name": "api-db-r1"},
        "spec": {
            "instances": 1,
            "bootstrap": {
                "recovery": {
                    "source": "origin",
                    "recoveryTarget": {
                        "backupID": "b1",
                        "targetImmediate": True,
                    },
                }
            },
            "externalClusters": [
                {
                    "name": "origin",
                    "plugin": {"parameters": {"serverName": "api-db"}},
                }
            ],
            "plugins": [{"name": restore.PLUGIN}],
        },
        "status": {"phase": "Setting up primary", "readyInstances": 0},
    }

    read = restore.database_read(cluster)

    assert (read.restored_from, read.restored_backup, read.healthy) == (
        "api-db",
        "b1",
        False,
    )
