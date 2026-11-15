"""
An app's traces, read from VictoriaTraces' Jaeger API in the project's own
tenant (D10, Q9). The tenant is the isolation: a trace ID of another
project is not found, whatever the caller sends.

Within the project, traces are the app's service (OTEL_SERVICE_NAME is the
app name, infra-templates) and, when the gateway set it, the app's prod
namespace: the collector derives k8s.namespace.name from the connection.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.observability import stores

RANGES = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}
TRACE_ID = re.compile(r"^[0-9a-f]{16,32}$")
NAMESPACE_TAG = "k8s.namespace.name"
MAX_SPANS = 1000
# Searched per page before the error and namespace filters keep some.
OVERFETCH = 4
SPAN_TAGS = 30


def _tags(raw: list[dict] | None) -> dict[str, Any]:
    return {t.get("key", ""): t.get("value") for t in raw or []}


def is_error(span: dict) -> bool:
    tags = _tags(span.get("tags"))
    return str(tags.get("error", "")).lower() == "true" or (
        str(tags.get("otel.status_code", "")).upper() == "ERROR"
    )


def _in_namespace(trace: dict, namespace: str) -> bool:
    found = {
        _tags(p.get("tags")).get(NAMESPACE_TAG)
        for p in (trace.get("processes") or {}).values()
    } - {None}
    return not found or namespace in found


def _root(spans: list[dict]) -> dict:
    ids = {s.get("spanID") for s in spans}
    for span in spans:
        parents = [
            r.get("spanID")
            for r in span.get("references") or []
            if r.get("refType") == "CHILD_OF"
        ]
        if not any(p in ids for p in parents):
            return span
    return spans[0]


def summary(trace: dict) -> dict[str, Any]:
    spans = trace.get("spans") or []
    processes = trace.get("processes") or {}
    root = _root(spans)
    start = min(s.get("startTime", 0) for s in spans)
    end = max(s.get("startTime", 0) + s.get("duration", 0) for s in spans)
    return {
        "trace_id": trace.get("traceID", ""),
        "root_service": (processes.get(root.get("processID")) or {}).get(
            "serviceName", ""
        ),
        "root_operation": root.get("operationName", ""),
        "start": datetime.fromtimestamp(start / 1e6, tz=timezone.utc),
        "duration_ms": (end - start) / 1000,
        "spans": len(spans),
        "errors": sum(1 for s in spans if is_error(s)),
        "services": sorted(
            {p.get("serviceName", "") for p in processes.values()}
        ),
    }


async def list_traces(  # pylint: disable=too-many-arguments
    scope: stores.Scope,
    account: int,
    range_name: str,
    limit: int,
    errors_only: bool,
    min_duration_ms: int | None,
    now: datetime,
) -> list[dict[str, Any]]:
    start = now - timedelta(seconds=RANGES[range_name])
    params: dict[str, Any] = {
        "service": scope.app,
        "start": int(start.timestamp() * 1e6),
        "end": int(now.timestamp() * 1e6),
        "limit": limit * OVERFETCH,
    }
    if min_duration_ms:
        params["minDuration"] = f"{min_duration_ms}ms"
    body = await stores.jaeger(account, "traces", params) or {}
    found = []
    for trace in body.get("data") or []:
        if not trace.get("spans") or not _in_namespace(trace, scope.namespace):
            continue
        row = summary(trace)
        if errors_only and not row["errors"]:
            continue
        found.append(row)
    found.sort(key=lambda r: r["start"], reverse=True)
    return found[:limit]


def _span(span: dict, processes: dict) -> dict[str, Any]:
    parent = next(
        (
            r.get("spanID")
            for r in span.get("references") or []
            if r.get("refType") == "CHILD_OF"
        ),
        None,
    )
    process = processes.get(span.get("processID")) or {}
    tags = list(_tags(span.get("tags")).items())[:SPAN_TAGS]
    return {
        "span_id": span.get("spanID", ""),
        "parent_id": parent,
        "service": process.get("serviceName", ""),
        "operation": span.get("operationName", ""),
        "start_us": span.get("startTime", 0),
        "duration_us": span.get("duration", 0),
        "error": is_error(span),
        "tags": {k: str(v)[:500] for k, v in tags},
    }


async def trace_detail(account: int, trace_id: str) -> dict[str, Any] | None:
    body = await stores.jaeger(account, f"traces/{trace_id}")
    data = (body or {}).get("data") or []
    if not data or not data[0].get("spans"):
        return None
    trace = data[0]
    processes = trace.get("processes") or {}
    spans = sorted(
        trace["spans"][:MAX_SPANS], key=lambda s: s.get("startTime", 0)
    )
    return {
        **summary({**trace, "spans": spans}),
        "truncated": len(trace["spans"]) > MAX_SPANS,
        "span_list": [_span(s, processes) for s in spans],
    }
