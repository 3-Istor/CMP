"""
Why an imported app did not become healthy in time (CNP-270), read from its
Argo CD Application, its pods and the warning events of its namespace.
"""

import logging
import re

from fastapi.concurrency import run_in_threadpool

from app.models.project import TargetCloud
from app.services import kube_client
from app.services.imports.failures import LOG_EXCERPT_MAX, public_text

logger = logging.getLogger(__name__)

MESSAGE_MAX = 500
LOG_TAIL_LINES = "60"
ARGO_ERRORS = ("ComparisonError", "InvalidSpecError", "SyncError")
# Logs of an app may print the credentials the platform generated for it.
CREDENTIAL = re.compile(
    r"(?i)\b([\w-]*(?:password|passwd|secret|token|auth)[\w-]*)"
    r"(\s*=\s*|:\s+)\S+"
)
URL_USERINFO = re.compile(r"://[^/\s:@]+:[^@\s/]+@")
PULL_ERRORS = (
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
    "ErrImageNeverPull",
)


def _scrub(text: str) -> str:
    text = URL_USERINFO.sub("://[redacted]@", text)
    return public_text(CREDENTIAL.sub(r"\1\2[redacted]", text))


def _readable(text: str) -> str:
    # Helm wraps the chart's own error in the whole command line it ran.
    if "Error: " in text:
        text = text.rsplit("Error: ", 1)[1]
    return _scrub(" ".join(text.split()))[:MESSAGE_MAX]


def _found(cause: str, message: str, **extra) -> dict:
    return {"cause": cause, "message": _readable(message), **extra}


def _argo_refusal(application: dict) -> dict | None:
    status = application.get("status") or {}
    for condition in status.get("conditions") or []:
        if condition.get("type") in ARGO_ERRORS and condition.get("message"):
            return _found("manifests_refused", condition["message"])
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
            state = container.get("state") or {}
            waiting = state.get("waiting") or {}
            last = (container.get("lastState") or {}).get("terminated")
            crashing = waiting.get("reason") == "CrashLoopBackOff" or bool(
                state.get("terminated")
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


def _when(event: dict) -> str:
    return event.get("lastTimestamp") or event.get("eventTime") or ""


def _latest(
    events: list[dict], reason: str, pod: set[str] | None = None
) -> dict | None:
    matching = [
        e
        for e in events
        if e.get("type") == "Warning"
        and e.get("reason") == reason
        and e.get("message")
        and (pod is None or (e.get("involvedObject") or {}).get("name") in pod)
    ]
    return max(matching, key=_when) if matching else None


def _probe_killed(found: dict, events: list[dict]) -> dict:
    """A container its liveness probe kills crash-loops for the probe."""
    event = _latest(events, "Unhealthy", {found["pod"]})
    if event and event["message"].startswith("Liveness probe failed"):
        return {**found, **_found("probe_failed", event["message"])}
    return found


def _health_message(application: dict) -> str:
    status = application.get("status") or {}
    health = status.get("health") or {}
    if health.get("status") == "Healthy":
        sync = (status.get("sync") or {}).get("status") or "Unknown"
        return (
            "Argo CD has not rolled out the new version yet (sync: " f"{sync})"
        )
    message = (
        f"Still {health.get('status') or 'not healthy'} after the allowed "
        "delay"
    )
    if health.get("message"):
        message += f": {health['message']}"
    return message


def classify(  # pylint: disable=too-many-return-statements
    application: dict | None,
    pods: list[dict],
    events: list[dict],
    since: str = "",
) -> dict:
    """
    The cause and a readable message, the first that applies in the order
    a deploy goes through: Argo CD renders and applies the chart, the pod is
    created and scheduled, its image pulled, its container started, then
    probed. A crash loop also names its pod and container, for the log.
    Events before *since* (RFC 3339, UTC) belong to an earlier version.
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
    events = [e for e in events if _when(e) >= since]
    found = _pull_error(live)
    if found:
        return found
    found = _crash_loop(live)
    if found:
        return _probe_killed(found, events)
    found = _unscheduled(live)
    if found:
        return found
    live_names = {p["metadata"]["name"] for p in live}
    for reason, cause, pod in (
        ("FailedScheduling", "unschedulable", None),
        ("FailedCreate", "unschedulable", None),
        ("Unhealthy", "probe_failed", live_names),
    ):
        event = _latest(events, reason, pod)
        if event:
            if reason == "FailedCreate" and "quota" not in event["message"]:
                return _found("manifests_refused", event["message"])
            return _found(cause, event["message"])
    return _found("deploy_timeout", _health_message(application))


def _read(
    project: str, app: str, cloud: TargetCloud
) -> tuple[dict | None, list, list]:
    """Argo CD runs on-prem; the app's pods on the project's cluster."""
    namespace = f"{project}-{app}"
    application = kube_client.kube_get(
        f"/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/"
        f"{project}-{app}-app"
    )
    pods = kube_client.kube_get(
        f"/api/v1/namespaces/{namespace}/pods", cloud=cloud
    )
    events = kube_client.kube_get(
        f"/api/v1/namespaces/{namespace}/events", cloud=cloud
    )
    return (
        application,
        (pods or {}).get("items") or [],
        (events or {}).get("items") or [],
    )


def _previous_log(
    namespace: str, pod: str, container: str, cloud: TargetCloud
) -> str | None:
    try:
        text = kube_client.kube_get_text(
            f"/api/v1/namespaces/{namespace}/pods/{pod}/log",
            {
                "container": container,
                "previous": "true",
                "tailLines": LOG_TAIL_LINES,
                "limitBytes": str(LOG_EXCERPT_MAX * 2),
            },
            cloud=cloud,
        )
    except kube_client.KubeUnavailableError as exc:
        logger.warning("No previous log for %s/%s: %s", namespace, pod, exc)
        return None
    if not text or not text.strip():
        return None
    return _scrub(text)[-LOG_EXCERPT_MAX:]


async def diagnose(
    project: str,
    app: str,
    cloud: TargetCloud = TargetCloud.ONPREM,
    since: str = "",
) -> dict:
    """classify() on the live cluster; a timeout when it cannot be read."""
    try:
        found = classify(
            *await run_in_threadpool(_read, project, app, cloud), since=since
        )
    except kube_client.KubeUnavailableError as exc:
        logger.warning("Diagnosis of %s-%s failed: %s", project, app, exc)
        return _found("deploy_timeout", "Not healthy after the allowed delay")
    pod = found.pop("pod", None)
    container = found.pop("container", None)
    if pod and container:
        log = await run_in_threadpool(
            _previous_log, f"{project}-{app}", pod, container, cloud
        )
        if log:
            found["log_excerpt"] = log
    return found
