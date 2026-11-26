"""
Data behind the overview of an app: metrics, recent logs and Kubernetes events.

Every selector is built here from the app's own project and namespace, read
from the database row; nothing the browser sends reaches a label matcher.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.services import activity_sources as sources
from app.services.environments import gitops, layout
from app.services.kube_client import kube_list
from app.services.metrics_isolation import VMSELECT, cluster_filter

WINDOWS = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}
LOG_WINDOWS = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}
POINTS = 96
RATE_RANGE = "5m"
EVENT_KINDS = {"Pod", "ReplicaSet", "Rollout", "Deployment"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_LEVEL_WORDS = (
    ("error", "error|err|fatal|panic|exception|critical"),
    ("warn", "warn|warning"),
    ("info", "info"),
    ("debug", "debug|trace"),
)
_LEVEL_RES = [
    (name, re.compile(rf"\b({words})\b", re.IGNORECASE))
    for name, words in _LEVEL_WORDS
]
_FIELD_RES = [
    (name, re.compile(f"^({words})$", re.IGNORECASE))
    for name, words in _LEVEL_WORDS
]


class MetricsUnavailableError(Exception):
    pass


@dataclass
class Scope:
    project: str
    app: str
    namespace: str
    cloud: TargetCloud
    components: list[str]
    environment: layout.Environment = "prod"


def scope_of(
    deployment: Deployment, environment: layout.Environment = "prod"
) -> Scope:
    project = deployment.project_id or ""
    app = deployment.name
    if not _NAME.match(project) or not _NAME.match(app):
        raise ValueError("invalid project or app name")
    return Scope(
        project=project,
        app=app,
        namespace=layout.namespace(project, app, environment),
        cloud=TargetCloud(gitops.target_cloud(deployment)),
        components=layout.components(gitops.app_type(deployment)),
        environment=environment,
    )


def _fullname(scope: Scope, component: str) -> str:
    return layout.chart_fullname(scope.project, scope.app, component)


# ── Metrics ──────────────────────────────────────────────────────────────────


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=VMSELECT, timeout=10)


async def _query(
    client: httpx.AsyncClient, path: str, params: dict[str, Any], cloud: str
) -> list[dict[str, Any]]:
    try:
        response = await client.get(
            path,
            params={**params, "extra_filters[]": cluster_filter(cloud)},
        )
        response.raise_for_status()
        return response.json()["data"]["result"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise MetricsUnavailableError(str(exc)) from exc


async def _series(
    client: httpx.AsyncClient,
    query: str,
    start: datetime,
    end: datetime,
    step: int,
    cloud: str,
) -> list[tuple[datetime, float]] | None:
    result = await _query(
        client,
        "/api/v1/query_range",
        {
            "query": query,
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "step": step,
        },
        cloud,
    )
    if not result:
        return None
    return [
        (datetime.fromtimestamp(float(ts), tz=timezone.utc), float(value))
        for ts, value in result[0]["values"]
        if value not in ("NaN", "+Inf", "-Inf")
    ]


def _route_selector(scope: Scope) -> str:
    return (
        'container="envoy", '
        f'envoy_cluster_name=~"httproute/{scope.namespace}/.*"'
    )


def error_ratio(
    total: list[tuple[datetime, float]],
    errors: list[tuple[datetime, float]] | None,
) -> list[tuple[datetime, float]]:
    failed = dict(errors or [])
    return [
        (when, 100 * failed.get(when, 0.0) / value)
        for when, value in total
        if value > 0
    ]


async def app_metrics(
    scope: Scope, window: str, now: datetime
) -> dict[str, Any]:
    seconds = WINDOWS[window]
    start = now - timedelta(seconds=seconds)
    step = max(60, seconds // POINTS)
    cloud = scope.cloud.value
    route = _route_selector(scope)
    cpu_selector = (
        f'namespace="{scope.namespace}", container="cnp-generic-app"'
    )
    async with _client() as client:
        total = await _series(
            client,
            f"sum(rate(envoy_cluster_upstream_rq_total{{{route}}}"
            f"[{RATE_RANGE}]))",
            start,
            now,
            step,
            cloud,
        )
        errors = (
            await _series(
                client,
                "sum(rate(envoy_cluster_upstream_rq_xx{"
                f'{route}, envoy_response_code_class="5"}}'
                f"[{RATE_RANGE}]))",
                start,
                now,
                step,
                cloud,
            )
            if total
            else None
        )
        cpu = await _series(
            client,
            "1000 * sum(rate(container_cpu_usage_seconds_total{"
            f"{cpu_selector}}}[{RATE_RANGE}]))",
            start,
            now,
            step,
            cloud,
        )
        requested = await _query(
            client,
            "/api/v1/query",
            {
                "query": "1000 * sum(kube_pod_container_resource_requests{"
                f'{cpu_selector}, resource="cpu"}})'
            },
            cloud,
        )
    return {
        "window": window,
        "step_seconds": step,
        "requests_per_second": total,
        "errors_5xx_percent": (
            None if total is None else error_ratio(total, errors)
        ),
        "cpu_millicores": cpu,
        "cpu_request_millicores": (
            float(requested[0]["value"][1]) if requested else None
        ),
    }


# ── Logs ─────────────────────────────────────────────────────────────────────


def level_of(line: str, field: str | None = None) -> str | None:
    """From the JSON line's level field when it has one, else its words."""
    if field:
        return next((n for n, p in _FIELD_RES if p.match(field)), None)
    head = line[:200]
    for name, pattern in _LEVEL_RES:
        if pattern.search(head):
            return name
    return None


def level_filter(level: str) -> str:
    words = dict(_LEVEL_WORDS)[level]
    by_words = sources.quoted(rf"(?i)\b({words})\b")
    by_field = sources.quoted(f"(?i)^({words})$")
    return f"(_msg:~{by_words} or json.level:~{by_field})"


def component_of(scope: Scope, pod: str) -> str | None:
    for component in scope.components:
        if pod.startswith(f"{_fullname(scope, component)}-"):
            return component
    return None


def logs_selector(scope: Scope, component: str | None) -> str:
    selector = [
        f"project={sources.quoted(scope.project)}",
        f"namespace={sources.quoted(scope.namespace)}",
    ]
    if component:
        pods = f"{_fullname(scope, component)}-.+"
        selector.append(f"pod=~{sources.quoted(pods)}")
    return "{" + ", ".join(selector) + "}"


def logs_query(
    scope: Scope, component: str | None, level: str | None, text: str | None
) -> str:
    query = logs_selector(scope, component)
    if text:
        query += f" {sources.contains(text)}"
    if level:
        query += f" {level_filter(level)}"
    return query


async def app_logs(
    scope: Scope,
    component: str | None,
    level: str | None,
    text: str | None,
    since: str,
    limit: int,
    now: datetime,
) -> list[dict[str, Any]]:
    lines = await sources.log_lines(
        logs_query(scope, component, level, text),
        now - timedelta(seconds=LOG_WINDOWS[since]),
        now,
        limit,
        streams=sources.project_streams(scope.project),
        fields=("json.level",),
    )
    return [
        {
            "time": when,
            "component": component_of(scope, labels.get("pod", "")),
            "pod": labels.get("pod", ""),
            "level": level_of(line, labels.get("json.level")),
            "line": line,
        }
        for when, labels, line in lines
    ]


# ── Events ───────────────────────────────────────────────────────────────────


def _event_time(event: dict[str, Any]) -> datetime:
    series = event.get("series") or {}
    raw = (
        series.get("lastObservedTime")
        or event.get("lastTimestamp")
        or event.get("eventTime")
        or event.get("metadata", {}).get("creationTimestamp")
    )
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _belongs(scope: Scope, event: dict[str, Any]) -> bool:
    ref = event.get("involvedObject") or {}
    if ref.get("kind") not in EVENT_KINDS:
        return False
    name = ref.get("name", "")
    return any(
        name == _fullname(scope, c)
        or name.startswith(f"{_fullname(scope, c)}-")
        for c in scope.components
    )


def app_events(scope: Scope, limit: int) -> list[dict[str, Any]]:
    items = kube_list(
        f"/api/v1/namespaces/{scope.namespace}/events", cloud=scope.cloud
    )
    mine = [e for e in items if _belongs(scope, e)]
    mine.sort(key=_event_time, reverse=True)
    return [
        {
            "time": _event_time(e),
            "type": e.get("type", "Normal"),
            "reason": e.get("reason", ""),
            "message": e.get("message", ""),
            "count": e.get("count")
            or (e.get("series") or {}).get("count")
            or 1,
            "kind": e["involvedObject"]["kind"],
            "object": e["involvedObject"]["name"],
        }
        for e in mine[:limit]
    ]
