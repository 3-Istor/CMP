"""
Why an imported app did not become healthy in time (CNP-270), read from its
Argo CD Application, its pods and the warning events of its namespace.
"""

import logging

from fastapi.concurrency import run_in_threadpool

from app.services import kube_client
from app.services.imports.failures import LOG_EXCERPT_MAX, public_text

logger = logging.getLogger(__name__)

MESSAGE_MAX = 500
LOG_TAIL_LINES = "60"
ARGO_ERRORS = ("ComparisonError", "InvalidSpecError", "SyncError")
PULL_ERRORS = (
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
    "ErrImageNeverPull",
)


def _readable(text: str) -> str:
    # Helm wraps the chart's own error in the whole command line it ran.
    if "Error: " in text:
        text = text.rsplit("Error: ", 1)[1]
    return public_text(" ".join(text.split()))[:MESSAGE_MAX]


def _found(cause: str, message: str, **extra) -> dict:
    return {"cause": cause, "message": _readable(message), **extra}


def _argo_refusal(application: dict) -> dict | None:
    status = application.get("status") or {}
    for condition in status.get("conditions") or []:
        if condition.get("type") in ARGO_ERRORS and condition.get("message"):
            return _found("manifests_refused", condition["message"])
    operation = status.get("operationState") or {}
    if operation.get("phase") in ("Failed", "Error") and operation.get(
        "message"
    ):
        return _found("manifests_refused", operation["message"])
    return None


def _containers(pod: dict) -> list[dict]:
    status = pod.get("status") or {}
    return (status.get("initContainerStatuses") or []) + (
        status.get("containerStatuses") or []
    )


def _pull_error(pods: list[dict]) -> dict | None:
    for pod in pods:
        for container in _containers(pod):
            waiting = (container.get("state") or {}).get("waiting") or {}
            if waiting.get("reason") in PULL_ERRORS:
                return _found(
                    "image_pull", waiting.get("message") or waiting["reason"]
                )
    return None


def _crash_loop(pods: list[dict]) -> dict | None:
    for pod in pods:
        for container in _containers(pod):
            waiting = (container.get("state") or {}).get("waiting") or {}
            last = (container.get("lastState") or {}).get("terminated")
            crashing = waiting.get("reason") == "CrashLoopBackOff" or (
                last and not container.get("ready")
            )
            if not crashing:
                continue
            message = (
                f"Container {container.get('name')} restarted "
                f"{container.get('restartCount', 0)} times"
            )
            if last:
                message += f", last exit code {last.get('exitCode')}"
                if last.get("reason"):
                    message += f" ({last['reason']})"
            return _found(
                "crash_loop",
                message,
                pod=pod["metadata"]["name"],
                container=container.get("name"),
            )
    return None


def _unscheduled(pods: list[dict]) -> dict | None:
    for pod in pods:
        for condition in (pod.get("status") or {}).get("conditions") or []:
            if (
                condition.get("type") == "PodScheduled"
                and condition.get("status") == "False"
            ):
                return _found(
                    "unschedulable",
                    condition.get("message") or "No node can run the pod",
                )
    return None


def _latest(events: list[dict], reason: str) -> dict | None:
    matching = [
        e
        for e in events
        if e.get("type") == "Warning" and e.get("reason") == reason
    ]
    if not matching:
        return None
    return max(
        matching,
        key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "",
    )


def classify(
    application: dict | None, pods: list[dict], events: list[dict]
) -> dict:
    """
    The cause and a readable message, the first that applies in the order
    a deploy goes through: Argo CD renders and applies the chart, the pod is
    created and scheduled, its image pulled, its container started, then
    probed. A crash loop also names its pod and container, for the log.
    """
    if application is None:
        return _found(
            "deploy_timeout", "Argo CD has not created the application yet"
        )
    refused = _argo_refusal(application)
    if refused:
        return refused
    live = [
        p for p in pods if (p.get("status") or {}).get("phase") != "Succeeded"
    ]
    for check in (_pull_error, _crash_loop, _unscheduled):
        found = check(live)
        if found:
            return found
    for reason, cause in (
        ("FailedScheduling", "unschedulable"),
        ("FailedCreate", "unschedulable"),
        ("Unhealthy", "probe_failed"),
    ):
        event = _latest(events, reason)
        if event and event.get("message"):
            if reason == "FailedCreate" and "quota" not in event["message"]:
                return _found("manifests_refused", event["message"])
            return _found(cause, event["message"])
    health = (application.get("status") or {}).get("health") or {}
    message = f"Still {health.get('status') or 'not healthy'} after the allowed delay"
    if health.get("message"):
        message += f": {health['message']}"
    return _found("deploy_timeout", message)


def _read(project: str, app: str) -> tuple[dict | None, list, list]:
    namespace = f"{project}-{app}"
    application = kube_client.kube_get(
        f"/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/"
        f"{project}-{app}-app"
    )
    pods = kube_client.kube_get(f"/api/v1/namespaces/{namespace}/pods")
    events = kube_client.kube_get(f"/api/v1/namespaces/{namespace}/events")
    return (
        application,
        (pods or {}).get("items") or [],
        (events or {}).get("items") or [],
    )


def _previous_log(namespace: str, pod: str, container: str) -> str | None:
    try:
        text = kube_client.kube_get_text(
            f"/api/v1/namespaces/{namespace}/pods/{pod}/log",
            {
                "container": container,
                "previous": "true",
                "tailLines": LOG_TAIL_LINES,
            },
        )
    except kube_client.KubeUnavailableError as exc:
        logger.info("No previous log for %s/%s: %s", namespace, pod, exc)
        return None
    if not text or not text.strip():
        return None
    return public_text(text)[-LOG_EXCERPT_MAX:]


async def diagnose(project: str, app: str) -> dict:
    """classify() on the live cluster; a timeout when it cannot be read."""
    try:
        found = classify(*await run_in_threadpool(_read, project, app))
    except kube_client.KubeUnavailableError as exc:
        logger.warning("Diagnosis of %s-%s failed: %s", project, app, exc)
        return _found("deploy_timeout", "Not healthy after the allowed delay")
    pod = found.pop("pod", None)
    container = found.pop("container", None)
    if pod and container:
        log = await run_in_threadpool(
            _previous_log, f"{project}-{app}", pod, container
        )
        if log:
            found["log_excerpt"] = log
    return found
