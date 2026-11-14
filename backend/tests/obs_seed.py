"""
Data for two projects in the real stores, as the platform writes it: Envoy
series of each app's routes and the series its SLO rules record
(VictoriaMetrics), Fluent Bit records with the app's JSON under json.*
(VictoriaLogs), spans in each project's own tenant (VictoriaTraces), and
alerts in Alertmanager. Used by the portal store tests and to run the
portal locally.
"""

import json
import math
import struct
import time

import httpx

from tests.obs_stack import _attribute, _bytes, _varint

GATEWAY = "envoy-gateway-system"
# Two apps that read the same in both projects: only the scope separates them.
APPS = {
    "shop": {"namespace": "shop-web", "app": "web", "account": 1},
    "rival": {"namespace": "rival-web", "app": "web", "account": 2},
}
SHOP_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SHOP_SLOW_TRACE = "5cf92f3577b34da6a3ce929d0e0e4737"
RIVAL_TRACE = "9aa92f3577b34da6a3ce929d0e0e4738"
RIVAL_SECRET = "rival card number 4111"
BUCKETS = ("25", "100", "250", "500", "1000", "+Inf")
STEP = 30


def _line(name: str, labels: dict[str, str], value: float, ts_ms: int) -> str:
    body = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{body}}} {value} {ts_ms}"


def _envoy(namespace: str, route: str) -> dict[str, str]:
    return {
        "namespace": GATEWAY,
        "container": "envoy",
        "envoy_cluster_name": f"httproute/{namespace}/{route}/rule/0",
    }


def metrics_lines(now: float, hours: int = 3) -> list[str]:
    """Counters that grow every STEP seconds over the last *hours*."""
    lines = []
    samples = hours * 3600 // STEP
    routes = {
        "shop-web": {"web": (12.0, 0.02), "web-api": (3.0, 0.10)},
        "rival-web": {"web": (50.0, 0.5)},
    }
    for namespace, by_route in routes.items():
        for route, (rps, error_share) in by_route.items():
            labels = _envoy(namespace, route)
            for i in range(samples + 1):
                ts = int((now - (samples - i) * STEP) * 1000)
                wave = 1 + 0.3 * math.sin(i / 20)
                total = rps * STEP * i * wave
                errors = total * error_share * (2 if i > samples * 0.8 else 1)
                lines.append(
                    _line("envoy_cluster_upstream_rq_total", labels, total, ts)
                )
                for code, value in (("2", total - errors), ("5", errors)):
                    lines.append(
                        _line(
                            "envoy_cluster_upstream_rq_xx",
                            {**labels, "envoy_response_code_class": code},
                            value,
                            ts,
                        )
                    )
                share = (0.3, 0.7, 0.9, 0.97, 0.995, 1.0)
                for le, part in zip(BUCKETS, share):
                    lines.append(
                        _line(
                            "envoy_cluster_upstream_rq_time_bucket",
                            {**labels, "le": le},
                            total * part,
                            ts,
                        )
                    )
    for project, spec in APPS.items():
        for kind, remaining in (("availability", 0.62), ("latency", 0.91)):
            slo = {
                "cnp_project": project,
                "cnp_app": spec["app"],
                "cnp_slo": kind,
                "namespace": spec["namespace"],
            }
            for i in range(0, samples + 1, 10):
                ts = int((now - (samples - i) * STEP) * 1000)
                left = remaining + 0.0005 * (samples - i)
                for name, value in (
                    ("slo:period_error_budget_remaining:ratio", left),
                    (
                        "slo:error_budget:ratio",
                        0.005 if kind == "availability" else 0.05,
                    ),
                    ("slo:sli_error:ratio_rate1h", 0.012),
                    ("slo:sli_error:ratio_rate6h", 0.006),
                    ("slo:sli_error:ratio_rate28d", 0.0019),
                    ("slo:sli_error:ratio_rate7d", 0.0019),
                ):
                    lines.append(_line(name, slo, value, ts))
    return lines


def log_records(now: float) -> list[dict]:
    def at(seconds_ago: float) -> str:
        return time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - seconds_ago)
        )

    def record(namespace, project, seconds_ago, **fields):
        base = {
            "date": at(seconds_ago),
            "cluster": "onprem",
            "namespace": namespace,
            "pod": f"{namespace}-backend-7c9f-abcde",
            "container": "cnp-generic-app",
            "project": project,
            "app": "web",
            "job": "kubernetes-pods",
        }
        return {**base, **fields}

    rows = []
    for i in range(12):
        rows.append(
            record(
                "shop-web",
                "shop",
                60 + i * 120,
                json={
                    "level": "error",
                    "message": f"payment provider timeout after {3000 + i} ms",
                    "trace_id": SHOP_TRACE,
                },
            )
        )
    for i in range(5):
        rows.append(
            record(
                "shop-web",
                "shop",
                90 + i * 300,
                json={
                    "level": "ERROR",
                    "message": f"order {1000 + i} not found",
                },
            )
        )
    rows.append(
        record(
            "shop-web",
            "shop",
            200,
            log='Traceback: KeyError "basket" in /app/cart.py line 42',
        )
    )
    for i in range(20):
        rows.append(
            record(
                "shop-web",
                "shop",
                30 + i * 60,
                json={
                    "level": "info",
                    "message": f"GET /api/items 200 in {20 + i} ms",
                    "trace_id": SHOP_SLOW_TRACE if i == 0 else None,
                },
            )
        )
    rows.append(
        record(
            "rival-web",
            "rival",
            100,
            json={
                "level": "error",
                "message": RIVAL_SECRET,
                "trace_id": RIVAL_TRACE,
            },
        )
    )
    return rows


def otlp_spans(  # pylint: disable=too-many-arguments
    trace_id: str,
    service: str,
    project: str,
    namespace: str,
    spans: list[tuple[str, str | None, str, float, float, bool]],
) -> bytes:
    """spans: (span_id, parent_id, name, start_s, duration_ms, error)."""
    attributes = (
        _bytes(1, _attribute("service.name", service))
        + _bytes(1, _attribute("cnp.project", project))
        + _bytes(1, _attribute("k8s.namespace.name", namespace))
    )
    encoded = b""
    for span_id, parent, name, start, duration_ms, error in spans:
        begin = int(start * 1e9)
        span = (
            _bytes(1, bytes.fromhex(trace_id))
            + _bytes(2, bytes.fromhex(span_id))
            + (_bytes(4, bytes.fromhex(parent)) if parent else b"")
            + _bytes(5, name.encode())
            + _varint(6 << 3)
            + _varint(2)
            + _varint(7 << 3 | 1)
            + struct.pack("<Q", begin)
            + _varint(8 << 3 | 1)
            + struct.pack("<Q", begin + int(duration_ms * 1e6))
        )
        if error:
            span += _bytes(15, _varint(3 << 3) + _varint(2))
        encoded += _bytes(2, span)
    return _bytes(1, _bytes(1, attributes) + _bytes(2, encoded))


def traces(now: float) -> list[tuple[int, bytes]]:
    start = now - 300
    return [
        (
            APPS["shop"]["account"],
            otlp_spans(
                SHOP_TRACE,
                "web",
                "shop",
                "shop-web",
                [
                    (
                        "a000000000000001",
                        None,
                        "POST /api/pay",
                        start,
                        3200,
                        True,
                    ),
                    (
                        "a000000000000002",
                        "a000000000000001",
                        "SELECT orders",
                        start + 0.01,
                        40,
                        False,
                    ),
                    (
                        "a000000000000003",
                        "a000000000000001",
                        "POST payment-provider",
                        start + 0.06,
                        3050,
                        True,
                    ),
                ],
            ),
        ),
        (
            APPS["shop"]["account"],
            otlp_spans(
                SHOP_SLOW_TRACE,
                "web",
                "shop",
                "shop-web",
                [
                    (
                        "b000000000000001",
                        None,
                        "GET /api/items",
                        start + 30,
                        180,
                        False,
                    ),
                    (
                        "b000000000000002",
                        "b000000000000001",
                        "SELECT items",
                        start + 30.02,
                        120,
                        False,
                    ),
                ],
            ),
        ),
        (
            APPS["rival"]["account"],
            otlp_spans(
                RIVAL_TRACE,
                "web",
                "rival",
                "rival-web",
                [("c000000000000001", None, "GET /secret", start, 50, False)],
            ),
        ),
    ]


def alerts() -> list[dict]:
    return [
        {
            "labels": {
                "alertname": "SloAvailabilityBurnRate",
                "severity": "critical",
                "cnp_project": project,
                "cnp_app": spec["app"],
                "cnp_slo": "availability",
                "slo_window": "1h",
                "namespace": spec["namespace"],
            },
            "annotations": {
                "summary": f"SLO de disponibilité de {spec['app']} : budget "
                "d'erreur brûlé 14.4x plus vite que prévu sur 1h",
                "description": "Objectif 99.5 % sur 28 jours.",
                "runbook_url": "https://example.invalid/slo-burn-rate.md",
            },
        }
        for project, spec in APPS.items()
    ]


def seed(
    metrics_url: str,
    logs_url: str,
    traces_url: str,
    alertmanager_url: str | None = None,
) -> None:
    now = time.time()
    httpx.post(
        f"{metrics_url}/api/v1/import/prometheus",
        content="\n".join(metrics_lines(now)) + "\n",
        timeout=60,
    ).raise_for_status()
    httpx.post(
        f"{logs_url}/insert/jsonline",
        params={
            "_stream_fields": "cluster,namespace,pod,container,project,app,job",
            "_msg_field": "log,json.message,json.msg",
            "_time_field": "date",
        },
        content="\n".join(
            json.dumps(
                {
                    **r,
                    **(
                        {"json": {k: v for k, v in r["json"].items() if v}}
                        if "json" in r
                        else {}
                    ),
                }
            )
            for r in log_records(now)
        ),
    ).raise_for_status()
    for account, body in traces(now):
        httpx.post(
            f"{traces_url}/insert/opentelemetry/v1/traces",
            headers={
                "Content-Type": "application/x-protobuf",
                "AccountID": str(account),
            },
            content=body,
        ).raise_for_status()
    for url in (metrics_url, logs_url, traces_url):
        httpx.get(f"{url}/internal/force_flush")
    if alertmanager_url:
        httpx.post(
            f"{alertmanager_url}/api/v2/alerts", json=alerts()
        ).raise_for_status()
