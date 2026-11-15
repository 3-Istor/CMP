"""
The CMP's own reads of the observability stores, for the app pages (V5).

Platform reads go to the stores directly, not through vmauth: the CMP pins
the scope itself, server side, from the app's database row. Every metrics
query carries extra_filters on the app's namespace (or its Envoy routes),
every logs query extra_stream_filters on it, and every traces call the
project's own AccountID. Nothing the browser sends reaches a selector.
"""

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.observability import TraceTenant
from app.services.metrics_isolation import ONPREM_FILTER
from app.services.observability.trace_tenants import PROJECT_ID

TIMEOUT = 15
# One tenant for logs (D6): the namespace is the isolation, not the tenant.
LOGS_TENANT = {"AccountID": "0", "ProjectID": "0"}


class NotAvailableError(Exception):
    """The store behind a signal is not configured yet."""

    def __init__(self, signal: str):
        super().__init__(signal)
        self.signal = signal


class StoreError(Exception):
    """The store is configured but did not answer."""


@dataclass(frozen=True)
class Scope:
    project: str
    app: str
    namespace: str
    cloud: str


def _client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT)


def capabilities() -> dict[str, bool]:
    return {
        "metrics": bool(settings.OBS_VMSINGLE_URL),
        "logs": bool(settings.OBS_VLOGS_URL),
        "traces": bool(
            settings.OBS_VTRACES_URL and settings.OBS_TRACES_ROUTING_ENABLED
        ),
        "alerts": settings.OBS_V3_ENABLED,
        "slo": settings.OBS_V3_ENABLED and bool(settings.OBS_VMSINGLE_URL),
    }


def require(signal: str) -> None:
    if not capabilities()[signal]:
        raise NotAvailableError(signal)


# ── Metrics ──────────────────────────────────────────────────────────────────


def _cluster(cloud: str) -> str:
    return ONPREM_FILTER[1:-1] if cloud == "onprem" else f'cluster="{cloud}"'


def metrics_filters(scope: Scope) -> list[str]:
    """
    VictoriaMetrics ORs the extra_filters it receives: the app's namespace
    (pods, recorded SLO series) or its own routes at the gateway.
    """
    cluster = _cluster(scope.cloud)
    return [
        f'{{{cluster}, namespace="{scope.namespace}"}}',
        f'{{{cluster}, namespace="{settings.OBS_GATEWAY_NAMESPACE}", '
        f'envoy_cluster_name=~"httproute/{scope.namespace}/.+"}}',
    ]


async def _metrics(
    scope: Scope, path: str, params: dict[str, Any]
) -> list[dict]:
    require("metrics")
    try:
        async with _client(settings.OBS_VMSINGLE_URL) as client:
            response = await client.post(
                f"/api/v1/{path}",
                data={**params, "extra_filters[]": metrics_filters(scope)},
            )
        response.raise_for_status()
        return response.json()["data"]["result"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise StoreError(f"metrics: {exc}") from exc


def _value(raw: str) -> float | None:
    value = float(raw)
    return value if math.isfinite(value) else None


async def instant(scope: Scope, query: str, at: datetime) -> list[dict]:
    """[{"metric": {...}, "value": float}], NaN and Inf left out."""
    result = await _metrics(
        scope, "query", {"query": query, "time": int(at.timestamp())}
    )
    found = []
    for row in result:
        value = _value(row["value"][1])
        if value is not None:
            found.append({"metric": row.get("metric", {}), "value": value})
    return found


async def series(
    scope: Scope, query: str, start: datetime, end: datetime, step: int
) -> list[dict]:
    """[{"metric": {...}, "points": [(datetime, float)]}]."""
    result = await _metrics(
        scope,
        "query_range",
        {
            "query": query,
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "step": step,
        },
    )
    found = []
    for row in result:
        points = []
        for ts, raw in row.get("values", []):
            value = _value(raw)
            if value is not None:
                points.append(
                    (datetime.fromtimestamp(float(ts), tz=timezone.utc), value)
                )
        found.append({"metric": row.get("metric", {}), "points": points})
    return found


# ── Logs ─────────────────────────────────────────────────────────────────────


def stream_filter(namespace: str) -> str:
    return f'{{namespace="{namespace}"}}'


async def logs(namespace: str, query: str, limit: int) -> list[dict]:
    """
    LogsQL rows of one namespace. extra_stream_filters is ANDed with the
    query by VictoriaLogs, so even a query that named another namespace
    would read nothing outside this one.
    """
    require("logs")
    try:
        async with _client(settings.OBS_VLOGS_URL) as client:
            response = await client.post(
                "/select/logsql/query",
                data={
                    "query": f"{stream_filter(namespace)} {query}",
                    "limit": limit,
                    "extra_stream_filters": stream_filter(namespace),
                },
                headers=LOGS_TENANT,
            )
        response.raise_for_status()
        return [
            json.loads(line)
            for line in response.text.splitlines()
            if line.strip()
        ]
    except (httpx.HTTPError, ValueError) as exc:
        raise StoreError(f"logs: {exc}") from exc


# ── Traces ───────────────────────────────────────────────────────────────────


def trace_account(project: str) -> int:
    """
    The project's VictoriaTraces tenant, read only: the collector tick
    allocates them. A project without one has no traces yet.
    """
    require("traces")
    with SessionLocal() as db:
        account = (
            db.query(TraceTenant.id)
            .filter(
                TraceTenant.project == project,
                TraceTenant.retired_at.is_(None),
            )
            .scalar()
        )
    if account is None:
        raise NotAvailableError("traces")
    return account


async def jaeger(
    account: int, path: str, params: dict[str, Any] | None = None
) -> dict | None:
    """None on 404 (an unknown trace, or one of another tenant)."""
    require("traces")
    try:
        async with _client(settings.OBS_VTRACES_URL) as client:
            response = await client.get(
                f"/select/jaeger/api/{path}",
                params=params,
                headers={
                    "AccountID": str(account),
                    "ProjectID": str(PROJECT_ID),
                },
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise StoreError(f"traces: {exc}") from exc


# ── Alerts ───────────────────────────────────────────────────────────────────


async def firing_alerts(project: str, app: str) -> list[dict]:
    """Alertmanager's alerts carrying exactly this project and app."""
    require("alerts")
    try:
        async with _client(settings.ALERTMANAGER_URL) as client:
            response = await client.get(
                "/api/v2/alerts",
                params=[
                    ("filter", f'cnp_project="{project}"'),
                    ("filter", f'cnp_app="{app}"'),
                ],
            )
        response.raise_for_status()
        found = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise StoreError(f"alerts: {exc}") from exc
    return [
        a
        for a in found
        if (a.get("labels") or {}).get("cnp_project") == project
        and (a.get("labels") or {}).get("cnp_app") == app
    ]
