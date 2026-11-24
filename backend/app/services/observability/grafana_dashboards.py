"""
The dashboards the CMP generates in each project's Grafana org, as small
table-driven builders.

Project orgs read through vmauth, which keeps only series that carry the
project's namespaces. The panels therefore use the series the SLO rules
record (cnp_slo:*, slo:*), kube-state-metrics and cAdvisor, and the
cnp_ns:* recording rules, never the raw gateway, Hubble or OpenCost series.
"""

from dataclasses import dataclass, field

DS = {"type": "prometheus", "uid": "${ds}"}
LOGS_DS_VAR = "logs"
LOGS_DS = {"type": "victoriametrics-logs-datasource", "uid": "${logs}"}

APPS_FOLDER = ("cnp-apps", "Apps")
DATASTORES_FOLDER = ("cnp-datastores", "Datastores")
DELIVERY_FOLDER = ("cnp-delivery", "Delivery")
LOGS_FOLDER = ("cnp-logs-traces", "Logs and traces")
AI_FOLDER = ("cnp-ai", "AI")
COST_FOLDER = ("cnp-cost", "Cost")
FOLDERS = [
    APPS_FOLDER,
    DATASTORES_FOLDER,
    DELIVERY_FOLDER,
    LOGS_FOLDER,
    AI_FOLDER,
    COST_FOLDER,
]

HOME_UID = "cnp-project-overview"
APP_UID = "cnp-app-signals"
DATASTORE_UID = "cnp-datastore"
DEPLOYMENTS_UID = "cnp-deployments"
LOG_EXPLORER_UID = "cnp-log-explorer"
AGENTS_UID = "cnp-agents-usage"
COST_UID = "cnp-project-cost"

DATASOURCE_VAR = {
    "name": "ds",
    "label": "Metrics",
    "type": "datasource",
    "query": "prometheus",
    "hide": 2,
}
LOGS_DATASOURCE_VAR = {
    "name": LOGS_DS_VAR,
    "label": "Logs",
    "type": "datasource",
    "query": "victoriametrics-logs-datasource",
    "hide": 2,
}

SLO_AVAIL = 'cnp_slo="availability"'
SLO_LATENCY = 'cnp_slo="latency"'
APP_SEL = 'cnp_app="$app", namespace=~"$env"'
CONTAINERS = 'container!="", container!="POD"'
BAD_STATES = [
    {"color": "green", "value": None},
    {"color": "orange", "value": 0.01},
    {"color": "red", "value": 0.05},
]
BUDGET_STATES = [
    {"color": "red", "value": None},
    {"color": "orange", "value": 0.25},
    {"color": "green", "value": 0.5},
]


@dataclass
class Panel:  # pylint: disable=too-many-instance-attributes
    title: str
    queries: list[tuple[str, str]]
    unit: str = "short"
    kind: str = "timeseries"
    width: int = 8
    height: int = 7
    steps: list[dict] | None = None
    description: str = ""
    extra: dict = field(default_factory=dict)


def q(expr: str, legend: str = "") -> list[tuple[str, str]]:
    return [(expr, legend)]


def _targets(panel: Panel) -> list[dict]:
    instant = panel.kind != "timeseries"
    return [
        {
            "datasource": DS,
            "expr": expr,
            "legendFormat": legend,
            "refId": chr(ord("A") + i),
            "instant": instant,
            "range": not instant,
        }
        for i, (expr, legend) in enumerate(panel.queries)
    ]


def _render(panel: Panel, panel_id: int, x: int, y: int) -> dict:
    defaults: dict = {"unit": panel.unit}
    if panel.steps:
        defaults["thresholds"] = {"mode": "absolute", "steps": panel.steps}
        defaults["color"] = {"mode": "thresholds"}
    body = {
        "id": panel_id,
        "type": panel.kind,
        "title": panel.title,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": panel.width, "h": panel.height},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "targets": _targets(panel),
        **panel.extra,
    }
    if panel.description:
        body["description"] = panel.description
    return body


def _row(title: str, panel_id: int, y: int) -> dict:
    return {
        "id": panel_id,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def layout(sections: list[tuple[str, list[Panel]]]) -> list[dict]:
    """Rows flow left to right on a 24 column grid, a section per row."""
    out: list[dict] = []
    y = 0
    next_id = 1
    for title, panels in sections:
        if title:
            out.append(_row(title, next_id, y))
            next_id += 1
            y += 1
        x = 0
        line_height = 0
        for panel in panels:
            if x + panel.width > 24:
                y += line_height
                x = 0
                line_height = 0
            out.append(_render(panel, next_id, x, y))
            next_id += 1
            x += panel.width
            line_height = max(line_height, panel.height)
        y += line_height
    return out


def variable(name: str, label: str, query: str, **extra) -> dict:
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DS,
        "query": {"query": query, "refId": "var"},
        "definition": query,
        "refresh": 2,
        "sort": 1,
        **extra,
    }


def deploy_annotation(env: str = "$env") -> dict:
    """A new ReplicaSet in the namespace is a new revision (Deployment or Rollout)."""
    sel = f'kube_replicaset_created{{namespace=~"{env}"}}'
    return {
        "name": "Deploys",
        "datasource": DS,
        "enable": True,
        "iconColor": "blue",
        "expr": f"(count({sel}) - count({sel} offset 10m)) > 0",
        "step": "5m",
        "titleFormat": "Deploy",
        "textFormat": "A new revision was created",
    }


def dashboard(
    uid: str,
    title: str,
    sections: list[tuple[str, list[Panel]]],
    variables: list[dict] | None = None,
    annotations: list[dict] | None = None,
    logs: bool = False,
) -> dict:
    ds_vars = [DATASOURCE_VAR] + ([LOGS_DATASOURCE_VAR] if logs else [])
    return {
        "uid": uid,
        "title": title,
        "tags": ["cnp", "generated"],
        "editable": False,
        "schemaVersion": 39,
        "time": {"from": "now-24h", "to": "now"},
        "refresh": "1m",
        "templating": {"list": [*ds_vars, *(variables or [])]},
        "annotations": {"list": annotations or []},
        "panels": layout(sections),
    }


def _app_link(label: str) -> list[dict]:
    return [
        {
            "title": f"Open {label}",
            "url": f"/d/{APP_UID}?var-app=${{__data.fields.cnp_app}}",
        }
    ]


def home_dashboard() -> dict:
    by_app = "cnp_app"
    reqs = f"sum by (cnp_app) (cnp_slo:requests:rate5m{{{SLO_AVAIL}}})"
    errs = f"sum by (cnp_app) (cnp_slo:errors:rate5m{{{SLO_AVAIL}}})"
    table = Panel(
        "Apps",
        [
            (
                f"sum by ({by_app}) (cnp_slo:requests:rate5m{{{SLO_AVAIL}}})",
                "Requests/s",
            ),
            (
                f"sum by ({by_app}) (cnp_slo:errors:rate5m{{{SLO_AVAIL}}})"
                f" / sum by ({by_app}) (cnp_slo:requests:rate5m"
                f"{{{SLO_AVAIL}}})",
                "5xx",
            ),
            (
                f"min by ({by_app}) (slo:period_error_budget_remaining:ratio"
                f"{{{SLO_AVAIL}}})",
                "Budget left",
            ),
        ],
        kind="table",
        width=24,
        height=8,
        extra={
            "transformations": [
                {
                    "id": "joinByField",
                    "options": {"byField": "cnp_app", "mode": "outer"},
                }
            ],
            "fieldConfig": {
                "defaults": {"links": _app_link("app dashboard")},
                "overrides": [],
            },
        },
        description="One row per app. Click a row to open the App dashboard.",
    )
    sections = [
        (
            "",
            [
                Panel(
                    "Requests per second by app",
                    q(reqs, "{{cnp_app}}"),
                    "reqps",
                    width=8,
                ),
                Panel(
                    "5xx share by app",
                    q(f"{errs} / {reqs}", "{{cnp_app}}"),
                    "percentunit",
                    width=8,
                ),
                Panel(
                    "Slow requests share by app (over 500 ms)",
                    q(
                        "sum by (cnp_app) (cnp_slo:errors:rate5m"
                        f"{{{SLO_LATENCY}}}) / sum by (cnp_app)"
                        f" (cnp_slo:requests:rate5m{{{SLO_LATENCY}}})",
                        "{{cnp_app}}",
                    ),
                    "percentunit",
                    width=8,
                ),
                Panel(
                    "Error log lines per minute",
                    q(
                        'sum(cnp_ns:log_lines:rate5m{level="error"}) * 60',
                        "error lines/min",
                    ),
                    width=12,
                ),
                Panel(
                    "Denied network flows per second",
                    q("sum(cnp_ns:denied_flows:rate5m)", "denied"),
                    width=12,
                ),
                table,
            ],
        )
    ]
    return dashboard(
        HOME_UID,
        "Project health",
        sections,
        annotations=[deploy_annotation(".+")],
    )


def app_dashboard() -> dict:
    avail = f"{APP_SEL}, {SLO_AVAIL}"
    latency = f"{APP_SEL}, {SLO_LATENCY}"
    ns = 'namespace=~"$env"'
    pods = f'{ns}, pod=~"$app-.*", {CONTAINERS}'
    requests = f"sum(cnp_slo:requests:rate5m{{{avail}}})"
    errors = f"sum(cnp_slo:errors:rate5m{{{avail}}})"
    sections = [
        (
            "Is it up? (RED)",
            [
                Panel(
                    "Availability",
                    q(
                        f"1 - ({errors} / {requests})",
                    ),
                    "percentunit",
                    "stat",
                    6,
                    5,
                    BUDGET_STATES,
                ),
                Panel(
                    "Requests/s now",
                    q(requests),
                    "reqps",
                    "stat",
                    6,
                    5,
                ),
                Panel(
                    "Slow requests share (over 500 ms)",
                    q(
                        f"sum(cnp_slo:errors:rate5m{{{latency}}})"
                        f" / sum(cnp_slo:requests:rate5m{{{latency}}})"
                    ),
                    "percentunit",
                    "stat",
                    6,
                    5,
                    BAD_STATES,
                ),
                Panel(
                    "Error budget left",
                    q(
                        "min(slo:period_error_budget_remaining:ratio"
                        f"{{{APP_SEL}}})"
                    ),
                    "percentunit",
                    "stat",
                    6,
                    5,
                    BUDGET_STATES,
                ),
                Panel("Requests per second", q(requests, "requests"), "reqps"),
                Panel(
                    "5xx share",
                    q(f"{errors} / {requests}", "5xx"),
                    "percentunit",
                ),
                Panel(
                    "Slow requests share over time",
                    q(
                        f"sum(cnp_slo:errors:rate5m{{{latency}}})"
                        f" / sum(cnp_slo:requests:rate5m{{{latency}}})",
                        "over 500 ms",
                    ),
                    "percentunit",
                ),
            ],
        ),
        (
            "Why? (USE and stability)",
            [
                Panel(
                    "CPU vs request and limit (cores)",
                    [
                        (
                            "sum(rate(container_cpu_usage_seconds_total"
                            f"{{{pods}}}[5m]))",
                            "usage",
                        ),
                        (
                            "sum(kube_pod_container_resource_requests"
                            f'{{{ns}, pod=~"$app-.*", resource="cpu"}})',
                            "request",
                        ),
                        (
                            "sum(kube_pod_container_resource_limits"
                            f'{{{ns}, pod=~"$app-.*", resource="cpu"}})',
                            "limit",
                        ),
                    ],
                ),
                Panel(
                    "Memory vs limit",
                    [
                        (
                            f"sum(container_memory_working_set_bytes{{{pods}}})",
                            "usage",
                        ),
                        (
                            "sum(kube_pod_container_resource_limits"
                            f'{{{ns}, pod=~"$app-.*", resource="memory"}})',
                            "limit",
                        ),
                    ],
                    "bytes",
                ),
                Panel(
                    "CPU throttling",
                    q(
                        "sum(rate(container_cpu_cfs_throttled_periods_total"
                        f"{{{pods}}}[5m])) / sum(rate("
                        f"container_cpu_cfs_periods_total{{{pods}}}[5m]))",
                        "throttled",
                    ),
                    "percentunit",
                ),
                Panel(
                    "Restarts over 1 h",
                    q(
                        "sum(increase(kube_pod_container_status_restarts_total"
                        f'{{{ns}, pod=~"$app-.*"}}[1h]))'
                    ),
                    kind="stat",
                    width=6,
                    height=5,
                ),
                Panel(
                    "OOM kills now",
                    q(
                        "sum(kube_pod_container_status_last_terminated_reason"
                        f'{{{ns}, pod=~"$app-.*", reason="OOMKilled"}})'
                    ),
                    kind="stat",
                    width=6,
                    height=5,
                ),
            ],
        ),
        (
            "What does it say? (logs, network)",
            [
                Panel(
                    "Log lines by level",
                    q(
                        f"sum by (level) (cnp_ns:log_lines:rate5m{{{ns}}}) * 60",
                        "{{level}}",
                    ),
                    description="Lines per minute for the whole namespace.",
                ),
                Panel(
                    "Denied flows",
                    q(f"sum(cnp_ns:denied_flows:rate5m{{{ns}}})", "denied"),
                ),
            ],
        ),
    ]
    return dashboard(
        APP_UID,
        "App",
        sections,
        [
            variable(
                "app",
                "App",
                f"label_values(cnp_slo:requests:rate5m{{{SLO_AVAIL}}}, cnp_app)",
            ),
            variable(
                "env",
                "Env",
                "label_values(cnp_slo:requests:rate5m"
                f'{{cnp_app="$app"}}, namespace)',
                multi=True,
                includeAll=True,
                current={"text": "All", "value": "$__all"},
            ),
        ],
        [deploy_annotation()],
    )


def datastore_dashboard() -> dict:
    pods = f'pod=~"$app-.*", {CONTAINERS}'
    sections = [
        (
            "",
            [
                Panel(
                    "CPU (cores)",
                    q(
                        "sum by (pod) (rate(container_cpu_usage_seconds_total"
                        f"{{{pods}}}[5m]))",
                        "{{pod}}",
                    ),
                    width=12,
                ),
                Panel(
                    "Memory",
                    q(
                        "sum by (pod) (container_memory_working_set_bytes"
                        f"{{{pods}}})",
                        "{{pod}}",
                    ),
                    "bytes",
                    width=12,
                ),
                Panel(
                    "Volume usage",
                    q(
                        "max by (persistentvolumeclaim)"
                        " (kubelet_volume_stats_used_bytes"
                        '{persistentvolumeclaim=~".*$app.*"})'
                        " / max by (persistentvolumeclaim)"
                        " (kubelet_volume_stats_capacity_bytes"
                        '{persistentvolumeclaim=~".*$app.*"})',
                        "{{persistentvolumeclaim}}",
                    ),
                    "percentunit",
                    steps=[
                        {"color": "green", "value": None},
                        {"color": "orange", "value": 0.8},
                        {"color": "red", "value": 0.9},
                    ],
                    width=12,
                ),
                Panel(
                    "Restarts over 1 h",
                    q(
                        "sum by (pod) (increase("
                        "kube_pod_container_status_restarts_total"
                        '{pod=~"$app-.*"}[1h]))',
                        "{{pod}}",
                    ),
                    width=12,
                ),
                Panel(
                    "Denied flows",
                    q("sum(cnp_ns:denied_flows:rate5m)", "denied"),
                    width=12,
                ),
            ],
        )
    ]
    return dashboard(
        DATASTORE_UID,
        "Datastore",
        sections,
        [
            variable(
                "app",
                "Datastore",
                "label_values(kube_statefulset_replicas, statefulset)",
            )
        ],
        [deploy_annotation(".+")],
    )


def deployments_dashboard() -> dict:
    created = "kube_replicaset_created"
    sections = [
        (
            "",
            [
                Panel(
                    "Revisions created over 24 h",
                    q(f"count({created} > (time() - 86400))"),
                    kind="stat",
                    width=6,
                    height=5,
                ),
                Panel(
                    "Replicas available vs desired",
                    [
                        (
                            "sum by (deployment)"
                            " (kube_deployment_status_replicas_available)",
                            "{{deployment}} available",
                        ),
                        (
                            "sum by (deployment)"
                            " (kube_deployment_spec_replicas)",
                            "{{deployment}} desired",
                        ),
                    ],
                    width=18,
                ),
                Panel(
                    "Latest revisions",
                    q(f"topk(15, {created} * 1000)", "{{replicaset}}"),
                    "dateTimeAsIso",
                    "table",
                    24,
                    9,
                    extra={
                        "transformations": [
                            {
                                "id": "organize",
                                "options": {
                                    "excludeByName": {
                                        "Time": True,
                                        "__name__": True,
                                    },
                                    "renameByName": {"Value": "Created"},
                                },
                            }
                        ]
                    },
                ),
                Panel(
                    "Pods not ready",
                    q(
                        "sum by (namespace) (kube_pod_status_ready"
                        '{condition="false"})',
                        "{{namespace}}",
                    ),
                    width=12,
                ),
                Panel(
                    "Restarts over 1 h",
                    q(
                        "sum by (namespace) (increase("
                        "kube_pod_container_status_restarts_total[1h]))",
                        "{{namespace}}",
                    ),
                    width=12,
                ),
            ],
        )
    ]
    return dashboard(
        DEPLOYMENTS_UID,
        "Deployments",
        sections,
        annotations=[deploy_annotation(".+")],
    )


def agents_dashboard() -> dict:
    sections = [
        (
            "",
            [
                Panel(
                    "Tokens per second by direction",
                    q(
                        "sum by (direction) (cnp_ns:ai_tokens:rate5m)",
                        "{{direction}}",
                    ),
                    width=12,
                ),
                Panel(
                    "Tokens per second by agent",
                    q(
                        "sum by (azp) (cnp_ns:ai_tokens:rate5m)",
                        "{{azp}}",
                    ),
                    width=12,
                ),
                Panel(
                    "Tokens over the range by agent",
                    q(
                        "sum by (azp) (avg_over_time("
                        "cnp_ns:ai_tokens:rate5m[$__range])) * $__range_s",
                        "{{azp}}",
                    ),
                    kind="bargauge",
                    width=24,
                    description="Average rate times the range, so an estimate.",
                ),
            ],
        )
    ]
    return dashboard(AGENTS_UID, "Agents usage", sections)


def cost_dashboard() -> dict:
    sections = [
        (
            "",
            [
                Panel(
                    "Cost now (EUR per hour)",
                    q("sum(cnp_ns:cost_eur:rate1h)"),
                    "currencyEUR",
                    "stat",
                    6,
                    5,
                ),
                Panel(
                    "Projected per month (EUR)",
                    q("sum(cnp_ns:cost_eur:rate1h) * 730"),
                    "currencyEUR",
                    "stat",
                    6,
                    5,
                ),
                Panel(
                    "Cost by namespace (EUR per hour)",
                    q(
                        "sum by (namespace) (cnp_ns:cost_eur:rate1h)",
                        "{{namespace}}",
                    ),
                    "currencyEUR",
                    width=12,
                ),
            ],
        )
    ]
    return dashboard(COST_UID, "Project cost", sections)


def log_explorer_dashboard() -> dict:
    scope = "$filter"
    logs_target = {
        "datasource": LOGS_DS,
        "expr": scope,
        "queryType": "instant",
        "refId": "A",
        "maxLines": 1000,
    }
    hits_target = {
        "datasource": LOGS_DS,
        "expr": f"{scope} | stats by (json.level) count() as lines",
        "queryType": "statsRange",
        "legendFormat": "{{json.level}}",
        "refId": "A",
    }
    panels = [
        {
            "id": 1,
            "type": "timeseries",
            "title": "Lines by level",
            "datasource": LOGS_DS,
            "gridPos": {"x": 0, "y": 0, "w": 24, "h": 7},
            "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
            "targets": [hits_target],
        },
        {
            "id": 2,
            "type": "logs",
            "title": "Logs",
            "datasource": LOGS_DS,
            "gridPos": {"x": 0, "y": 7, "w": 24, "h": 18},
            "options": {"showTime": True, "wrapLogMessage": True},
            "targets": [logs_target],
        },
    ]
    base = dashboard(LOG_EXPLORER_UID, "Log explorer", [], logs=True)
    base["templating"]["list"].append(
        {
            "name": "filter",
            "label": "LogsQL filter",
            "type": "textbox",
            "query": "*",
            "current": {"text": "*", "value": "*"},
        }
    )
    base["panels"] = panels
    return base


DASHBOARDS = [
    (APPS_FOLDER, app_dashboard),
    (DATASTORES_FOLDER, datastore_dashboard),
    (DELIVERY_FOLDER, deployments_dashboard),
    (AI_FOLDER, agents_dashboard),
    (COST_FOLDER, cost_dashboard),
]
