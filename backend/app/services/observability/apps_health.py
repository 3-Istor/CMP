"""
The health of every app of a project in one read, for the project page:
a state per app, its few numbers and a sparkline, and the stacked series by
app. The cost does not grow with the number of apps: one Argo CD list, one
schedules list, one Alertmanager read and two metrics queries (instant and
range), each grouped by namespace and pinned to the project's namespaces.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func

from app.core import database
from app.core.config import settings
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.project import Project, TargetCloud
from app.models.release import ReleaseEvent
from app.services import topology
from app.services.app_health import RESTART_THRESHOLD
from app.services.environments import gitops, layout
from app.services.observability import delivery, signals, stores
from app.services.observability.summary import (
    STATUS_ORDER,
    Grid,
    aware,
    by_tag,
    kind_of,
    resource_kinds,
    status_of,
)

logger = logging.getLogger(__name__)

# Names reach PromQL label matchers and regexes.
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
POINTS = 96
SPARK_POINTS = 24
# Progressing and suspended are a deploy at work, not a problem.
ARGO_DOWN = {"missing"}
ARGO_DEGRADED = {"degraded"}


@dataclass
class AppRow:
    id: int
    name: str
    template_id: str
    components: list[str]
    last_deploy: dict | None = None


@dataclass
class ProjectInput:
    project: str
    cloud: str
    apps: list[AppRow] = field(default_factory=list)

    def namespaces(self) -> dict[str, str]:
        return {
            layout.namespace(self.project, a.name, "prod"): a.name
            for a in self.apps
        }


def read_input(project: str) -> ProjectInput:
    """The project's Kubernetes apps and their last prod deploy. Blocking."""
    if not _NAME.match(project):
        raise ValueError(f"invalid project name {project!r}")
    with database.SessionLocal() as db:
        cloud = (
            db.query(Project.target_cloud)
            .filter(Project.project_name == project)
            .scalar()
        )
        rows = (
            db.query(Deployment)
            .filter(
                Deployment.project_id == project,
                Deployment.provider_type == ProviderType.KUBERNETES,
                Deployment.status.not_in(
                    (DeploymentStatus.DELETED, DeploymentStatus.DELETING)
                ),
            )
            .order_by(Deployment.name)
            .all()
        )
        ids = [r.id for r in rows]
        latest = (
            db.query(
                ReleaseEvent.deployment_id,
                func.max(ReleaseEvent.started_at).label("at"),
            )
            .filter(
                ReleaseEvent.deployment_id.in_(ids),
                ReleaseEvent.environment == "prod",
                ReleaseEvent.outcome.in_(delivery.SUCCEEDED),
            )
            .group_by(ReleaseEvent.deployment_id)
            .subquery()
        )
        deploys = {
            e.deployment_id: {
                "version": delivery.version_of(e.image) or f"r{e.revision}",
                "at": aware(e.started_at),
            }
            for e in db.query(ReleaseEvent).join(
                latest,
                and_(
                    ReleaseEvent.deployment_id == latest.c.deployment_id,
                    ReleaseEvent.started_at == latest.c.at,
                ),
            )
        }
        return ProjectInput(
            project=project,
            cloud=(cloud or TargetCloud.ONPREM).value,
            apps=[
                AppRow(
                    id=r.id,
                    name=r.name,
                    template_id=r.template_id,
                    components=layout.components(gitops.app_type(r)),
                    last_deploy=deploys.get(r.id),
                )
                for r in rows
                if _NAME.match(r.name)
            ],
        )


# ── Queries ──────────────────────────────────────────────────────────────────


def _slo(project: str, record: str) -> str:
    return (
        f'cnp_slo:{record}:rate5m{{cnp_project="{project}", '
        'cnp_slo="availability"}'
    )


def _envoy_by_namespace(pattern: str, window: str) -> str:
    gateway = (
        f'namespace="{settings.OBS_GATEWAY_NAMESPACE}", '
        f'container="envoy", envoy_cluster_name=~"httproute/{pattern}/.+"'
    )
    buckets = (
        f"label_replace(rate(envoy_cluster_upstream_rq_time_bucket"
        f'{{{gateway}}}[{window}]), "namespace", "$1", '
        '"envoy_cluster_name", "httproute/([^/]+)/.+")'
    )
    return f"histogram_quantile(0.95, sum by (namespace, le) ({buckets}))"


def instant_queries(project: str, pattern: str, window: str) -> dict[str, str]:
    ns = f'namespace=~"{pattern}"'
    pods = f'{ns}, container!="", container!="POD"'
    return {
        "rps": f"sum by (namespace) (avg_over_time({_slo(project, 'requests')}"
        f"[{window}]))",
        "errors": "sum by (namespace) (avg_over_time("
        f"{_slo(project, 'errors')}[{window}]))",
        "p95": _envoy_by_namespace(pattern, window),
        "cpu_ratio": "sum by (namespace) (rate(container_cpu_usage_seconds_total"
        f"{{{pods}}}[{signals.RATE}])) / sum by (namespace) "
        f'(kube_pod_container_resource_requests{{{ns}, resource="cpu"}})',
        "mem_ratio": "sum by (namespace) (container_memory_working_set_bytes"
        f"{{{pods}}}) / sum by (namespace) "
        f'(kube_pod_container_resource_limits{{{ns}, resource="memory"}})',
        "disk_ratio": "max by (namespace) (kubelet_volume_stats_used_bytes"
        f"{{{ns}}} / kubelet_volume_stats_capacity_bytes{{{ns}}})",
        "ready": "sum by (namespace) "
        f'(kube_pod_status_ready{{{ns}, condition="true"}})',
        "restarts": "sum by (namespace) (increase("
        f"kube_pod_container_status_restarts_total{{{ns}}}[15m]))",
    }


def range_queries(project: str, pattern: str, step: int) -> dict[str, str]:
    pods = f'namespace=~"{pattern}", container!="", container!="POD"'
    requests = f"sum by (namespace) ({_slo(project, 'requests')})"
    return {
        "rps": requests,
        "err_pct": f"100 * sum by (namespace) ({_slo(project, 'errors')})"
        f" / {requests}",
        "cpu": "sum by (namespace) (rate(container_cpu_usage_seconds_total"
        f"{{{pods}}}[{signals.RATE}]))",
        # Recorded once a minute from VictoriaLogs: already per minute.
        "error_logs": "sum(avg_over_time(cnp_log_error_lines:count1m"
        f'{{project="{project}"}}[{step}s]))',
    }


# ── State and shape ──────────────────────────────────────────────────────────


def base_state(  # pylint: disable=too-many-return-statements
    argo_health: list[str] | None,
    asleep: bool | None,
    ready: float | None,
    restarts: float | None,
) -> tuple[str, str | None]:
    """The app's state from Argo CD, its schedule and its pods' metrics."""
    if asleep:
        return "asleep", None
    if not argo_health and ready is None:
        return "unknown", "not_observed"
    if ready is not None and ready <= 0:
        return "down", "no_ready_replica"
    healths = set(argo_health or [])
    if healths & ARGO_DOWN:
        return "down", "missing"
    if healths & ARGO_DEGRADED:
        return "degraded", "argo_degraded"
    if restarts is not None and restarts >= RESTART_THRESHOLD:
        return "degraded", "restarts"
    return "healthy", None


def spark(values: list[float | None], points: int = SPARK_POINTS) -> list:
    """*values* averaged down to *points* buckets; None where all are None."""
    size = len(values)
    if size == 0:
        return [None] * points
    out: list[float | None] = []
    for i in range(points):
        chunk = [
            v
            for v in values[i * size // points : (i + 1) * size // points]
            if v is not None
        ]
        out.append(round(sum(chunk) / len(chunk), 4) if chunk else None)
    return out


def sort_apps(apps: list[dict]) -> list[dict]:
    """Down first, then degraded, unknown and healthy; by name within."""
    return sorted(
        apps, key=lambda a: (STATUS_ORDER.index(a["status"]), a["name"])
    )


def _argo_healths(
    applications: dict[str, dict], project: str, row: AppRow
) -> list[str] | None:
    found = [
        applications.get(layout.application(project, row.name, c, "prod"))
        for c in row.components
    ]
    healths = [
        str(
            ((item.get("status") or {}).get("health") or {}).get("status", "")
        ).lower()
        for item in found
        if item is not None
    ]
    return healths or None


def _ok(name: str, value: Any, default: Any) -> Any:
    if isinstance(value, BaseException):
        logger.warning("apps health: %s not readable: %s", name, value)
        return default
    return value


async def _firing_by_app(project: str) -> dict[str, int]:
    if not stores.capabilities()["alerts"]:
        return {}
    return await stores.firing_by_app(project)


async def apps_health(  # pylint: disable=too-many-locals
    data: ProjectInput, range_name: str, now: datetime
) -> dict[str, Any]:
    grid = Grid.of(range_name, now, POINTS)
    start, end = grid.bounds()
    if not data.apps:
        return {"apps": [], "series": _empty_series(grid)}
    by_namespace = data.namespaces()
    scope = stores.ProjectScope(
        project=data.project,
        namespaces=tuple(by_namespace),
        cloud=data.cloud,
    )
    pattern = stores.namespace_pattern(scope)
    metrics = stores.capabilities()["metrics"]

    async def nothing() -> list:
        return []

    parts = {
        "argo": asyncio.to_thread(topology.argo_by_name, data.project),
        "asleep": asyncio.to_thread(
            topology.asleep_by_namespace,
            data.project,
            TargetCloud(data.cloud),
        ),
        "alerts": _firing_by_app(data.project),
        "instant": (
            stores.instant(
                scope,
                stores.tagged(
                    instant_queries(data.project, pattern, range_name)
                ),
                end,
            )
            if metrics
            else nothing()
        ),
        "series": (
            stores.series(
                scope,
                stores.tagged(range_queries(data.project, pattern, grid.step)),
                start,
                end,
                grid.step,
            )
            if metrics
            else nothing()
        ),
    }
    found = dict(
        zip(
            parts,
            await asyncio.gather(*parts.values(), return_exceptions=True),
        )
    )
    argo = _ok("argo", found["argo"], {})
    asleep = _ok("asleep", found["asleep"], None)
    alerts = _ok("alerts", found["alerts"], {})
    instant = by_tag(_ok("instant", found["instant"], []), "namespace")
    ranged = {
        key: grid.align(points)
        for key, points in by_tag(
            _ok("series", found["series"], []), "namespace"
        ).items()
    }

    apps = []
    series: dict[str, Any] = {
        "t": grid.millis(),
        "rps_by_app": {},
        "err_pct_by_app": {},
        "error_logs_per_min": ranged.get(("error_logs", ""), []),
    }
    for row in data.apps:
        ns = layout.namespace(data.project, row.name, "prod")

        def value(tag: str, ns: str = ns) -> float | None:
            return instant.get((tag, ns))

        kind = kind_of(
            row.template_id,
            resource_kinds(argo, data.project, row.name, row.components),
        )
        rps = value("rps")
        error_pct = (
            round(100 * (value("errors") or 0.0) / rps, 3) if rps else None
        )
        state, reason = base_state(
            _argo_healths(argo, data.project, row),
            None if asleep is None else asleep.get(ns, False),
            value("ready"),
            value("restarts"),
        )
        status, status_reason = status_of(
            state, reason, error_pct, alerts.get(row.name, 0)
        )
        rps_series = ranged.get(("rps", ns))
        if rps_series is not None:
            series["rps_by_app"][row.name] = rps_series
        if ("err_pct", ns) in ranged:
            series["err_pct_by_app"][row.name] = ranged[("err_pct", ns)]
        spark_source = rps_series if kind == "web" else ranged.get(("cpu", ns))
        apps.append(
            {
                "id": row.id,
                "name": row.name,
                "kind": kind,
                "status": status,
                "status_reason": status_reason,
                "rps": round(rps, 4) if rps is not None else None,
                "error_pct": error_pct,
                "p95_ms": value("p95"),
                "cpu_ratio": value("cpu_ratio"),
                "mem_ratio": value("mem_ratio"),
                "disk_ratio": value("disk_ratio"),
                "spark": spark(spark_source or []),
                "last_deploy": row.last_deploy,
            }
        )
    return {"apps": sort_apps(apps), "series": series}


def _empty_series(grid: Grid) -> dict[str, Any]:
    return {
        "t": grid.millis(),
        "rps_by_app": {},
        "err_pct_by_app": {},
        "error_logs_per_min": [],
    }
