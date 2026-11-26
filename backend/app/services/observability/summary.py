"""
Everything above the fold of an app page in one read (CMP UX rework): its
kind and state, the tiles, the traffic, resource and log series on one time
grid, the last prod deploys, who calls it and its top errors.

Every part is read at the same time and each one may fail alone: a store
that does not answer leaves its fields empty, never the whole summary.
Metrics come in two queries (one instant, one range), each a union of
tagged sub-queries, all pinned to the app's namespace like the other
observability reads.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core import database
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import TargetCloud
from app.models.release import ReleaseEvent
from app.services import app_health, topology
from app.services.environments import gitops, layout
from app.services.kube_client import kube_list
from app.services.observability import (
    app_logs,
    delivery,
    signals,
    slo,
    stores,
)

logger = logging.getLogger(__name__)

TAG = "cnp_q"
ERROR_PCT_DEGRADED = 5.0
DEPLOYS = 3
# Enough rows for three deploys of a fullstack app, one row per component.
DEPLOY_EVENTS = 12
TOP_ERRORS = 3
DENIED_WINDOW = "24h"
BACKUPS_PATH = "/apis/postgresql.cnpg.io/v1/namespaces/{}/backups"
STATUS_ORDER = ("down", "degraded", "unknown", "healthy")


# ── Kind and status ──────────────────────────────────────────────────────────


def kind_of(template_id: str, resource_kinds: set[str] | None) -> str:
    """
    datastore: an imported backing service, or a workload on StatefulSets
    with no route; web: anything with an HTTPRoute; else worker. Without
    Argo CD's view, the template decides: only imported services may lack
    a route.
    """
    if template_id == "import-backing":
        return "datastore"
    if resource_kinds:
        if "HTTPRoute" in resource_kinds:
            return "web"
        if "StatefulSet" in resource_kinds:
            return "datastore"
        return "worker"
    return "worker" if template_id == "import-service" else "web"


def status_of(
    state: str,
    reason: str | None,
    error_pct: float | None,
    alerts_firing: int,
) -> tuple[str, str | None]:
    """The four states of the UI from the pods' state, 5xx and alerts."""
    if state in ("down", "degraded"):
        return state, reason
    if state == "asleep":
        return "unknown", "asleep"
    if state != "healthy":
        return "unknown", reason or state
    if error_pct is not None and error_pct >= ERROR_PCT_DEGRADED:
        return "degraded", "errors_5xx"
    if alerts_firing:
        return "degraded", "alerts_firing"
    return "healthy", None


def resource_kinds(
    applications: dict[str, dict],
    project: str,
    app: str,
    components: list,
    environment: layout.Environment = "prod",
) -> set[str] | None:
    """Kinds Argo CD manages for the app in one env; None when it shows none."""
    kinds: set[str] = set()
    seen = False
    for component in components:
        item = applications.get(
            layout.application(project, app, component, environment)
        )
        if item is None:
            continue
        seen = True
        for resource in (item.get("status") or {}).get("resources") or []:
            kinds.add(str(resource.get("kind", "")))
    return kinds if seen else None


# ── Time grid ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Grid:
    start: int
    step: int
    size: int

    @classmethod
    def of(cls, range_name: str, now: datetime, points: int = 0) -> "Grid":
        """*points* steps over the range, or the signals' own step."""
        seconds = signals.RANGES[range_name]
        step = signals.step_of(range_name)
        if points:
            step = max(60, seconds // points)
        end = int(now.timestamp()) // step * step
        size = seconds // step
        return cls(start=end - (size - 1) * step, step=step, size=size)

    @property
    def end(self) -> int:
        return self.start + (self.size - 1) * self.step

    def bounds(self) -> tuple[datetime, datetime]:
        return (
            datetime.fromtimestamp(self.start, tz=timezone.utc),
            datetime.fromtimestamp(self.end, tz=timezone.utc),
        )

    def millis(self) -> list[int]:
        return [(self.start + i * self.step) * 1000 for i in range(self.size)]

    def align(
        self, points: list[tuple[datetime, float]], fill: float | None = None
    ) -> list[float | None]:
        values: list[float | None] = [fill] * self.size
        for when, value in points:
            i = round((when.timestamp() - self.start) / self.step)
            if 0 <= i < self.size:
                values[i] = value
        return values


def last_value(values: list[float | None]) -> float | None:
    return next((v for v in reversed(values) if v is not None), None)


def by_tag(rows: list[dict], key: str = "") -> dict[Any, Any]:
    """Tagged results by tag (and by *key* label when given)."""
    found: dict[Any, Any] = {}
    for row in rows:
        tag = row["metric"].get(TAG, "")
        index = (tag, row["metric"].get(key, "")) if key else tag
        found[index] = row["points"] if "points" in row else row["value"]
    return found


# ── Queries ──────────────────────────────────────────────────────────────────


def _pods(namespace: str) -> str:
    return f'namespace="{namespace}", container!="", container!="POD"'


def instant_queries(namespace: str, window: str) -> dict[str, str]:
    over = signals.queries(namespace, window)
    envoy = slo.envoy_selector(namespace)
    ns = f'namespace="{namespace}"'
    return {
        "requests": (
            f"sum(increase(envoy_cluster_upstream_rq_total{{{envoy}}}"
            f"[{window}]))"
        ),
        "errors": (
            f"sum(increase(envoy_cluster_upstream_rq_xx{{{envoy}, "
            f'envoy_response_code_class="5"}}[{window}]))'
        ),
        "p50": over["p50"],
        "p95": over["p95"],
        "restarts": (
            "sum(increase(kube_pod_container_status_restarts_total"
            f"{{{ns}}}[{window}]))"
        ),
        "cpu_request": (
            f'sum(kube_pod_container_resource_requests{{{ns}, resource="cpu"}})'
        ),
        "mem_limit": (
            "sum(kube_pod_container_resource_limits"
            f'{{{ns}, resource="memory"}})'
        ),
        "disk_used": f"sum(kubelet_volume_stats_used_bytes{{{ns}}})",
        "disk_capacity": f"sum(kubelet_volume_stats_capacity_bytes{{{ns}}})",
    }


def range_queries(namespace: str) -> dict[str, str]:
    now = signals.queries(namespace)
    pods = _pods(namespace)
    return {
        "rps": now["rate"],
        "err_rps": now["errors"],
        "p95_ms": now["p95"],
        "cpu_cores": (
            f"sum(rate(container_cpu_usage_seconds_total{{{pods}}}"
            f"[{signals.RATE}]))"
        ),
        "mem_bytes": f"sum(container_memory_working_set_bytes{{{pods}}})",
    }


def log_levels_query() -> str:
    return (
        "| stats"
        f" count() if ({app_logs.level_filter('error')}) error,"
        f" count() if ({app_logs.level_filter('warn')}) warn,"
        " count() total"
    )


def log_series(rows: list[dict], grid: Grid) -> dict[str, list[float | None]]:
    """Lines per minute by level; info is every line not warn or error."""
    counts = {
        row["metric"].get("__name__", ""): grid.align(row["points"], 0.0)
        for row in rows
    }
    zero = [0.0] * grid.size
    error = counts.get("error", zero)
    warn = counts.get("warn", zero)
    total = counts.get("total", zero)
    per_minute = 60 / grid.step
    return {
        "log_info": [
            round(max(0.0, (t or 0) - (e or 0) - (w or 0)) * per_minute, 3)
            for t, e, w in zip(total, error, warn)
        ],
        "log_warn": [round((w or 0) * per_minute, 3) for w in warn],
        "log_error": [round((e or 0) * per_minute, 3) for e in error],
    }


# ── Deploys ──────────────────────────────────────────────────────────────────


def _outcome(outcomes: set[str]) -> str:
    if "running" in outcomes:
        return "running"
    if outcomes & delivery.FAILED:
        return "failed"
    if outcomes & delivery.SUCCEEDED:
        return "succeeded"
    if "superseded" in outcomes:
        return "replaced"
    return "unknown"


def deploy_rows(events: list[dict], now: datetime) -> list[dict]:
    """
    The last prod deploys, newest first, from release events (newest
    first). A fullstack app writes one event per component for the same
    version: they make one deploy. The newest succeeded deploy is live,
    older ones were replaced.
    """
    grouped: list[dict] = []
    for event in events:
        version = event["version"]
        if grouped and grouped[-1]["version"] == version:
            group = grouped[-1]
            group["started"] = min(group["started"], event["started_at"])
            if event["finished_at"] and (
                group["finished"] is None
                or event["finished_at"] > group["finished"]
            ):
                group["finished"] = event["finished_at"]
            group["outcomes"].add(event["outcome"])
            group["by"] = group["by"] or event["by"]
            continue
        grouped.append(
            {
                "version": version,
                "started": event["started_at"],
                "finished": event["finished_at"],
                "outcomes": {event["outcome"]},
                "by": event["by"],
            }
        )
    rows = []
    live_seen = False
    newer_start: datetime | None = None
    for group in grouped:
        outcome = _outcome(group["outcomes"])
        if outcome == "succeeded":
            outcome = "replaced" if live_seen else "live"
            live_seen = True
        finished = group["finished"]
        row: dict[str, Any] = {
            "version": group["version"],
            "at": group["started"],
            "outcome": outcome,
            "ready_seconds": None,
            "live_seconds": None,
            "by": group["by"],
        }
        if finished and outcome in ("live", "replaced", "failed"):
            row["ready_seconds"] = round(
                (finished - group["started"]).total_seconds()
            )
        if finished and outcome in ("live", "replaced"):
            until = now if outcome == "live" else (newer_start or now)
            row["live_seconds"] = max(
                0, round((until - finished).total_seconds())
            )
        rows.append(row)
        newer_start = group["started"]
        if len(rows) == DEPLOYS:
            break
    return rows


# ── The summary ──────────────────────────────────────────────────────────────


@dataclass
class AppInput:
    """What the database says about the app, read before any store."""

    deployment_id: int
    scope: stores.Scope
    template_id: str
    components: list[str]
    project_apps: list[str]
    events: list[dict] = field(default_factory=list)


def aware(value: datetime | None) -> datetime | None:
    # SQLite drops the timezone of DateTime(timezone=True) columns.
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _event(event: ReleaseEvent) -> dict:
    actor = next(
        (a.get("user") for a in event.actors or [] if a.get("user")), None
    )
    return {
        "version": delivery.version_of(event.image) or f"r{event.revision}",
        "started_at": aware(event.started_at),
        "finished_at": aware(event.finished_at),
        "outcome": event.outcome,
        "by": actor,
    }


def read_input(deployment_id: int, scope: stores.Scope) -> AppInput:
    """
    Its own session: a cached summary is refreshed after the request that
    asked for it has closed its own. Blocking: call it in a thread.
    """
    with database.SessionLocal() as db:
        deployment = db.get(Deployment, deployment_id)
        if deployment is None:
            raise LookupError(deployment_id)
        names = [
            name
            for (name,) in db.query(Deployment.name).filter(
                Deployment.project_id == deployment.project_id,
                Deployment.provider_type == ProviderType.KUBERNETES,
                Deployment.status.not_in(
                    (DeploymentStatus.DELETED, DeploymentStatus.DELETING)
                ),
            )
        ]
        events = (
            db.query(ReleaseEvent)
            .filter(
                ReleaseEvent.deployment_id == deployment_id,
                ReleaseEvent.environment == scope.environment,
            )
            .order_by(ReleaseEvent.started_at.desc())
            .limit(DEPLOY_EVENTS)
            .all()
        )
        return AppInput(
            deployment_id=deployment_id,
            scope=scope,
            template_id=deployment.template_id,
            components=layout.components(gitops.app_type(deployment)),
            project_apps=names,
            events=[_event(e) for e in events],
        )


def _read_health(
    deployment_id: int, now: datetime, environment: layout.Environment
) -> Any:
    with database.SessionLocal() as db:
        deployment = db.get(Deployment, deployment_id)
        # pylint: disable-next=protected-access
        return app_health._read_app_health(deployment, now, environment)


def _last_backup(namespace: str, cloud: str) -> datetime | None:
    done = [
        (b.get("status") or {}).get("stoppedAt")
        for b in kube_list(
            BACKUPS_PATH.format(namespace), cloud=TargetCloud(cloud)
        )
        if (b.get("status") or {}).get("phase") == "completed"
    ]
    stamps = [
        datetime.fromisoformat(s.replace("Z", "+00:00")) for s in done if s
    ]
    return max(stamps) if stamps else None


async def _alerts(scope: stores.Scope) -> int:
    # Alert rules are prod-only: a staging app has no alert to count.
    if scope.environment != "prod" or not stores.capabilities()["alerts"]:
        return 0
    found = await stores.firing_alerts(scope.project, scope.app)
    return sum(
        1
        for a in found
        if (a.get("status") or {}).get("state", "active") == "active"
    )


async def _logs(scope: stores.Scope, grid: Grid) -> list[dict] | None:
    if scope.cloud != "onprem" or not stores.capabilities()["logs"]:
        return None
    start, end = grid.bounds()
    return await stores.log_counts(
        scope.namespace, log_levels_query(), start, end, grid.step
    )


async def _top_errors(scope: stores.Scope, range_name: str) -> list[dict]:
    if scope.cloud != "onprem" or not stores.capabilities()["logs"]:
        return []
    rows = await app_logs.top_errors(scope, range_name, TOP_ERRORS)
    return [{"message": r["pattern"], "count": r["count"]} for r in rows]


def _ok(name: str, value: Any, default: Any) -> Any:
    if isinstance(value, BaseException):
        logger.warning("summary: %s not readable: %s", name, value)
        return default
    return value


async def app_summary(  # pylint: disable=too-many-locals
    data: AppInput, range_name: str, now: datetime
) -> dict[str, Any]:
    scope = data.scope
    grid = Grid.of(range_name, now)
    start, end = grid.bounds()
    metrics = stores.capabilities()["metrics"]

    async def nothing(value: Any) -> Any:
        return value

    parts = {
        "health": asyncio.to_thread(
            _read_health, data.deployment_id, now, scope.environment
        ),
        "argo": asyncio.to_thread(topology.argo_by_name, scope.project),
        "instant": (
            stores.instant(
                scope,
                stores.tagged(instant_queries(scope.namespace, range_name)),
                end,
            )
            if metrics
            else nothing([])
        ),
        "series": (
            stores.series(
                scope,
                stores.tagged(range_queries(scope.namespace)),
                start,
                end,
                grid.step,
            )
            if metrics
            else nothing([])
        ),
        "logs": _logs(scope, grid),
        "top_errors": _top_errors(scope, range_name),
        "alerts": _alerts(scope),
        "flows": topology.observed_flows(
            scope.project,
            data.project_apps,
            scope.cloud,
            environment=scope.environment,
        ),
        "denied": topology.denied_flows(
            scope.namespace, scope.cloud, DENIED_WINDOW
        ),
        "backup": asyncio.to_thread(
            _last_backup, scope.namespace, scope.cloud
        ),
    }
    found = dict(
        zip(
            parts,
            await asyncio.gather(*parts.values(), return_exceptions=True),
        )
    )
    health = _ok("health", found["health"], None)
    argo = _ok("argo", found["argo"], {})
    instant = by_tag(_ok("instant", found["instant"], []))
    series_rows = by_tag(_ok("series", found["series"], []))
    log_rows = _ok("logs", found["logs"], None)
    alerts_firing = _ok("alerts", found["alerts"], 0)
    _, flows = _ok("flows", found["flows"], (False, {}))
    denied = _ok("denied", found["denied"], None)

    kind = kind_of(
        data.template_id,
        resource_kinds(
            argo,
            scope.project,
            scope.app,
            data.components,
            scope.environment,
        ),
    )
    series: dict[str, Any] = {"t": grid.millis()}
    for name in ("rps", "err_rps", "p95_ms", "cpu_cores", "mem_bytes"):
        if name in series_rows:
            series[name] = grid.align(series_rows[name])
    series.update(
        log_series(log_rows, grid)
        if log_rows is not None
        else {"log_info": [], "log_warn": [], "log_error": []}
    )

    tiles = _tiles(kind, instant, series, health)
    if kind == "datastore":
        tiles["last_backup_at"] = _ok("backup", found["backup"], None)
    state, reason = _health_state(health)
    status, status_reason = status_of(
        state, reason, tiles.get("error_pct"), alerts_firing
    )
    return {
        "kind": kind,
        "status": status,
        "status_reason": status_reason,
        "tiles": tiles,
        "series": series,
        "deploys": deploy_rows(data.events, now),
        "clients": sorted(
            (
                {
                    "app": source,
                    "verdict": (
                        "allowed" if flow["forwarded"] > 0 else "denied"
                    ),
                    "flows": int(flow["count"]),
                }
                for (source, target), flow in flows.items()
                if target == scope.app
            ),
            key=lambda c: (-c["flows"], c["app"]),
        ),
        "denied_flows_24h": round(denied or 0),
        "top_errors": _ok("top_errors", found["top_errors"], []),
        "alerts_firing": alerts_firing,
    }


def _health_state(health: Any) -> tuple[str, str | None]:
    if health is None:
        return "unknown", "health_unreadable"
    reason = next(
        (c.reason for c in health.components if c.state == health.status),
        None,
    )
    return health.status, reason


def _ratio(value: float | None, of: float | None) -> float | None:
    return round(value / of, 4) if value is not None and of else None


def _tiles(
    kind: str, instant: dict, series: dict, health: Any
) -> dict[str, Any]:
    cpu = last_value(series.get("cpu_cores", []))
    mem = last_value(series.get("mem_bytes", []))
    tiles: dict[str, Any] = {
        "restarts": round(instant.get("restarts", 0)),
        "cpu_cores": cpu,
        "cpu_request_cores": instant.get("cpu_request"),
        "cpu_ratio": _ratio(cpu, instant.get("cpu_request")),
        "mem_bytes": mem,
        "mem_limit_bytes": instant.get("mem_limit"),
        "mem_ratio": _ratio(mem, instant.get("mem_limit")),
        "disk_bytes": instant.get("disk_used"),
        "disk_capacity_bytes": instant.get("disk_capacity"),
        "replicas_ready": (
            sum(c.ready for c in health.components) if health else 0
        ),
        "replicas_desired": (
            sum(c.desired for c in health.components) if health else 0
        ),
    }
    if kind == "web":
        requests = instant.get("requests")
        errors = instant.get("errors", 0.0)
        error_pct = round(100 * errors / requests, 3) if requests else None
        tiles.update(
            {
                "requests_total": round(requests) if requests else 0,
                "errors_5xx": round(errors),
                "error_pct": error_pct,
                "availability_pct": (
                    None if error_pct is None else round(100 - error_pct, 3)
                ),
                "p50_ms": instant.get("p50"),
                "p95_ms": instant.get("p95"),
            }
        )
    return tiles
