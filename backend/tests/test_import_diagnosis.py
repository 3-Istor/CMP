"""Why an imported app did not become healthy, from Argo CD and Kubernetes (CNP-270)."""

import asyncio

from app.services.imports import diagnosis

HEALTHY_APP = {
    "status": {
        "sync": {"status": "Synced"},
        "health": {"status": "Progressing"},
    }
}


def _pod(name="web-1", phase="Running", containers=(), conditions=()):
    return {
        "metadata": {"name": name},
        "status": {
            "phase": phase,
            "containerStatuses": list(containers),
            "conditions": list(conditions),
        },
    }


def _event(reason, message, when="2026-11-14T10:00:00Z", kind="Warning"):
    return {
        "type": kind,
        "reason": reason,
        "message": message,
        "lastTimestamp": when,
    }


def test_a_chart_render_refused_by_argo_says_which_guard_failed():
    application = {
        "status": {
            "sync": {"status": "Unknown"},
            "health": {"status": "Missing"},
            "conditions": [
                {
                    "type": "ComparisonError",
                    "message": "Failed to load target state: failed to "
                    "generate manifest for source 1 of 1: rpc error: code = "
                    "Unknown desc = Manifest generation error (cached): "
                    "`helm template . --name-template epitweet-neo4j` failed "
                    "exit status 1: Error: execution error at "
                    "(import-backing/templates/externalsecret.yaml:4:6): "
                    "vaultPath is required",
                }
            ],
        }
    }

    found = diagnosis.classify(application, [], [])

    assert found["cause"] == "manifests_refused"
    assert found["message"] == (
        "execution error at (import-backing/templates/externalsecret.yaml"
        ":4:6): vaultPath is required"
    )


def test_an_image_that_cannot_be_pulled_is_named():
    pod = _pod(
        phase="Pending",
        containers=[
            {
                "name": "app",
                "ready": False,
                "restartCount": 0,
                "state": {
                    "waiting": {
                        "reason": "ImagePullBackOff",
                        "message": 'Back-off pulling image "ghcr.io/3-istor/'
                        'imports/epitweet-web@sha256:abc"',
                    }
                },
            }
        ],
    )

    found = diagnosis.classify(HEALTHY_APP, [pod], [])

    assert found["cause"] == "image_pull"
    assert "ghcr.io/3-istor/imports/epitweet-web" in found["message"]


def test_a_crash_loop_gives_the_restarts_and_the_last_exit():
    pod = _pod(
        containers=[
            {
                "name": "neo4j",
                "ready": False,
                "restartCount": 6,
                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                "lastState": {
                    "terminated": {"exitCode": 1, "reason": "Error"}
                },
            }
        ]
    )

    found = diagnosis.classify(HEALTHY_APP, [pod], [])

    assert found["cause"] == "crash_loop"
    assert found["message"] == (
        "Container neo4j restarted 6 times, last exit code 1 (Error)"
    )
    assert (found["pod"], found["container"]) == ("web-1", "neo4j")


def test_a_container_killed_for_memory_says_so():
    pod = _pod(
        containers=[
            {
                "name": "app",
                "ready": False,
                "restartCount": 2,
                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                "lastState": {
                    "terminated": {"exitCode": 137, "reason": "OOMKilled"}
                },
            }
        ]
    )

    found = diagnosis.classify(HEALTHY_APP, [pod], [])

    assert "OOMKilled" in found["message"]


def test_a_pod_no_node_can_take_reports_the_scheduler_message():
    pod = _pod(
        phase="Pending",
        conditions=[
            {
                "type": "PodScheduled",
                "status": "False",
                "reason": "Unschedulable",
                "message": "0/3 nodes are available: 3 Insufficient memory.",
            }
        ],
    )

    found = diagnosis.classify(HEALTHY_APP, [pod], [])

    assert found == {
        "cause": "unschedulable",
        "message": "0/3 nodes are available: 3 Insufficient memory.",
    }


def test_a_pod_refused_by_the_namespace_quota_is_unschedulable():
    events = [
        _event(
            "FailedCreate",
            'Error creating: pods "web-7f9" is forbidden: exceeded quota: '
            "imports, requested: requests.memory=1Gi",
        )
    ]

    found = diagnosis.classify(HEALTHY_APP, [], events)

    assert found["cause"] == "unschedulable"
    assert "exceeded quota" in found["message"]


def test_a_running_container_never_ready_reports_the_failing_probe():
    pod = _pod(
        containers=[
            {
                "name": "app",
                "ready": False,
                "restartCount": 0,
                "state": {"running": {}},
            }
        ]
    )
    events = [
        _event(
            "Unhealthy", "Readiness probe failed: old", "2026-11-14T09:00:00Z"
        ),
        _event(
            "Unhealthy",
            "Readiness probe failed: HTTP probe failed with statuscode: 404",
            "2026-11-14T10:00:00Z",
        ),
    ]

    found = diagnosis.classify(HEALTHY_APP, [pod], events)

    assert found == {
        "cause": "probe_failed",
        "message": "Readiness probe failed: HTTP probe failed with "
        "statuscode: 404",
    }


def test_an_app_argo_never_created_is_a_timeout():
    found = diagnosis.classify(None, [], [])

    assert found["cause"] == "deploy_timeout"
    assert "Argo CD" in found["message"]


def test_without_a_known_cause_the_argo_health_is_reported():
    application = {
        "status": {
            "sync": {"status": "Synced"},
            "health": {
                "status": "Progressing",
                "message": "Waiting for rollout to finish",
            },
        }
    }

    found = diagnosis.classify(application, [], [])

    assert found == {
        "cause": "deploy_timeout",
        "message": "Still Progressing after the allowed delay: Waiting for "
        "rollout to finish",
    }


def test_messages_never_carry_a_token():
    pod = _pod(
        phase="Pending",
        containers=[
            {
                "name": "app",
                "ready": False,
                "restartCount": 0,
                "state": {
                    "waiting": {
                        "reason": "ErrImagePull",
                        "message": "auth with ghp_" + "a" * 36 + " refused",
                    }
                },
            }
        ],
    )

    found = diagnosis.classify(HEALTHY_APP, [pod], [])

    assert "ghp_" not in found["message"]


def test_a_crash_loop_diagnosis_carries_the_crashed_container_log(
    monkeypatch,
):
    pod = _pod(
        name="neo4j-0",
        containers=[
            {
                "name": "neo4j",
                "ready": False,
                "restartCount": 3,
                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                "lastState": {"terminated": {"exitCode": 1}},
            }
        ],
    )
    reads = []

    def get(path, params=None):
        if path.endswith("/applications/epitweet-neo4j-app"):
            return HEALTHY_APP
        if path.endswith("/pods"):
            return {"items": [pod]}
        return {"items": []}

    def get_text(path, params=None):
        reads.append((path, params))
        return "Invalid value for NEO4J_AUTH\n"

    monkeypatch.setattr(diagnosis.kube_client, "kube_get", get)
    monkeypatch.setattr(diagnosis.kube_client, "kube_get_text", get_text)

    found = asyncio.run(diagnosis.diagnose("epitweet", "neo4j"))

    assert found["cause"] == "crash_loop"
    assert found["log_excerpt"] == "Invalid value for NEO4J_AUTH"
    assert reads[0][0] == (
        "/api/v1/namespaces/epitweet-neo4j/pods/neo4j-0/log"
    )
    assert reads[0][1]["previous"] == "true"


def test_a_cluster_that_cannot_be_read_still_gives_a_timeout(monkeypatch):
    def down(path, params=None):
        raise diagnosis.kube_client.KubeUnavailableError("down")

    monkeypatch.setattr(diagnosis.kube_client, "kube_get", down)

    found = asyncio.run(diagnosis.diagnose("epitweet", "neo4j"))

    assert found["cause"] == "deploy_timeout"
