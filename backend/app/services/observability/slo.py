"""
The default SLO of every routed prod app (D14), as a VMRule.

Two objectives read from the Envoy metrics of the app's routes: availability
(non-5xx responses) and latency (responses under 500 ms, the p95 objective
written as a ratio). Same rule model as Sloth, generated here because the CMP
is Python: recording rules per window, then multi-window multi-burn-rate
alerts from the Google SRE Workbook.

Isolation: every selector names the app's namespace, and the group's
extra_filters make VictoriaMetrics drop any series outside the app's
namespace, its project's system namespace and the Envoy series of its own
routes. A rule cannot read another project even if its expression were wrong.
"""

from dataclasses import dataclass
from urllib.parse import quote

from app.core.config import settings

KINDS = ("availability", "latency")
WINDOWS = ("5m", "30m", "1h", "6h", "3d")
DEMO_LEVELS = frozenset({"sandbox", "demo"})
LATENCY_THRESHOLD_MS = 500
DEFAULT_AVAILABILITY = 99.5
DEFAULT_LATENCY = 95.0
# A request answered while the app slept, or while it woke up, is not the
# app's fault: offhours-guard flips the gauge back before pods are ready.
ASLEEP_LOOKBACK = "10m"


@dataclass(frozen=True)
class BurnAlert:
    severity: str
    factor: float
    long: str
    short: str
    long_hours: int
    wait: str


# Factors and windows of the Workbook's table. Sloth scales factors to the
# period (13.44/5.6/0.93 at 28 days); fixed factors keep "14.4x" meaning the
# same on every SLO, 7 or 28 days.
BURN_ALERTS = (
    BurnAlert("critical", 14.4, "1h", "5m", 1, "2m"),
    BurnAlert("warning", 6, "6h", "30m", 6, "15m"),
    BurnAlert("info", 1, "3d", "6h", 72, "1h"),
)

ALERT_NAMES = {
    "availability": "SloAvailabilityBurnRate",
    "latency": "SloLatencyBurnRate",
}
TITLES = {
    "availability": "disponibilité",
    "latency": f"latence (< {LATENCY_THRESHOLD_MS} ms)",
}


@dataclass(frozen=True)
class AppSlo:
    project: str
    app: str
    app_id: int
    namespace: str
    period_days: int
    availability: float = DEFAULT_AVAILABILITY
    latency: float = DEFAULT_LATENCY

    def target(self, kind: str) -> float:
        return self.availability if kind == "availability" else self.latency

    def budget(self, kind: str) -> float:
        return round(1 - self.target(kind) / 100, 6)


def period_days(stake_level: str) -> int:
    return 7 if stake_level in DEMO_LEVELS else 28


def object_name(project: str, app: str) -> str:
    return f"slo-{project}-{app}"


def _num(value: float) -> str:
    return f"{value:.6g}"


def _envoy_selector(namespace: str) -> str:
    return (
        f'namespace="{settings.OBS_GATEWAY_NAMESPACE}", container="envoy", '
        f'envoy_cluster_name=~"httproute/{namespace}/.+"'
    )


def extra_filters(slo: AppSlo) -> list[str]:
    """vmselect ORs these: the app's namespaces, or its routes at Envoy."""
    return [
        f'{{namespace=~"{slo.namespace}|{slo.project}-system"}}',
        f'{{namespace="{settings.OBS_GATEWAY_NAMESPACE}", '
        f'envoy_cluster_name=~"httproute/{slo.namespace}/.+"}}',
    ]


def _awake(expr: str, namespace: str) -> str:
    label = settings.OBS_OFFHOURS_NAMESPACE_LABEL
    return (
        f"({expr}) unless on () "
        f'(max(max_over_time(offhours_target_asleep{{{label}="{namespace}"}}'
        f"[{ASLEEP_LOOKBACK}])) > 0)"
    )


def _requests(kind: str, namespace: str) -> str:
    sel = _envoy_selector(namespace)
    metric = (
        "envoy_cluster_upstream_rq_xx"
        if kind == "availability"
        else "envoy_cluster_upstream_rq_time_count"
    )
    return f"sum by (cluster) (rate({metric}{{{sel}}}[5m]))"


def _errors(kind: str, namespace: str) -> str:
    sel = _envoy_selector(namespace)
    total = _requests(kind, namespace)
    if kind == "availability":
        failed = (
            "sum by (cluster) (rate(envoy_cluster_upstream_rq_xx"
            f'{{{sel}, envoy_response_code_class="5"}}[5m]))'
        )
    else:
        fast = (
            "sum by (cluster) (rate(envoy_cluster_upstream_rq_time_bucket"
            f'{{{sel}, le=~"{LATENCY_THRESHOLD_MS}|{LATENCY_THRESHOLD_MS}.0"}}'
            "[5m]))"
        )
        failed = f"{total} - {fast}"
    # No 5xx series at all means no error, not no data.
    return f"({failed}) or (0 * {total})"


def _selector(slo: AppSlo, kind: str) -> str:
    return (
        f'cnp_project="{slo.project}", cnp_app="{slo.app}", '
        f'cnp_slo="{kind}", namespace="{slo.namespace}"'
    )


def _labels(slo: AppSlo, kind: str) -> dict[str, str]:
    return {
        "cnp_project": slo.project,
        "cnp_app": slo.app,
        "cnp_slo": kind,
        "namespace": slo.namespace,
    }


def portal_url(slo: AppSlo) -> str:
    return (
        f"{settings.CMP_PUBLIC_URL}/projects/{slo.project}/apps/{slo.app_id}"
    )


def silence_url(slo: AppSlo, alertname: str) -> str:
    return (
        f"{settings.CMP_PUBLIC_URL}/projects/{slo.project}/alerts"
        f"?silence={quote(alertname)}&app={quote(slo.app)}"
    )


def _recordings(slo: AppSlo, kind: str) -> list[dict]:
    labels = _labels(slo, kind)
    sel = _selector(slo, kind)
    rules = [
        {
            "record": "cnp_slo:requests:rate5m",
            "expr": _awake(_requests(kind, slo.namespace), slo.namespace),
            "labels": labels,
        },
        {
            "record": "cnp_slo:errors:rate5m",
            "expr": _awake(_errors(kind, slo.namespace), slo.namespace),
            "labels": labels,
        },
        {
            "record": "slo:sli_error:ratio_rate5m",
            "expr": (
                f"cnp_slo:errors:rate5m{{{sel}}}"
                f" / cnp_slo:requests:rate5m{{{sel}}}"
            ),
            "labels": labels,
        },
    ]
    # Long windows from the awake 5m rates, so a night asleep weighs nothing.
    for window in WINDOWS[1:]:
        rules.append(
            {
                "record": f"slo:sli_error:ratio_rate{window}",
                "expr": (
                    f"sum_over_time(cnp_slo:errors:rate5m{{{sel}}}[{window}])"
                    " / sum_over_time("
                    f"cnp_slo:requests:rate5m{{{sel}}}[{window}])"
                ),
                "labels": labels,
            }
        )
    return rules


def _alerts(slo: AppSlo, kind: str) -> list[dict]:
    sel = _selector(slo, kind)
    budget = slo.budget(kind)
    name = ALERT_NAMES[kind]
    minimum = settings.OBS_SLO_MIN_REQUESTS_PER_HOUR
    rules = []
    for burn in BURN_ALERTS:
        threshold = _num(burn.factor * budget)
        expr = (
            f"(slo:sli_error:ratio_rate{burn.long}{{{sel}}} > {threshold}"
            f" and slo:sli_error:ratio_rate{burn.short}{{{sel}}}"
            f" > {threshold})"
            f" and (avg_over_time(cnp_slo:requests:rate5m{{{sel}}}"
            f"[{burn.long}]) * 3600 >= {minimum})"
        )
        rules.append(
            {
                "alert": name,
                "expr": expr,
                "for": burn.wait,
                "labels": {
                    **_labels(slo, kind),
                    "severity": burn.severity,
                    "slo_window": burn.long,
                },
                "annotations": {
                    "summary": (
                        f"SLO de {TITLES[kind]} de {slo.app} : budget "
                        f"d'erreur brûlé {_num(burn.factor)}x plus vite "
                        f"que prévu sur {burn.long}"
                    ),
                    "description": (
                        f"Objectif {_num(slo.target(kind))} % sur "
                        f"{slo.period_days} jours. À ce rythme, le budget "
                        "de la période dure "
                        f"{slo.period_days * 24 / burn.factor:.0f} h."
                    ),
                    "value": (
                        "{{ $value | humanizePercentage }} des requêtes "
                        f"hors objectif sur {burn.long} (seuil "
                        f"{_num(burn.factor * budget * 100)} %)"
                    ),
                    "runbook_url": (
                        f"{settings.OBS_RUNBOOK_BASE_URL}/slo-burn-rate.md"
                    ),
                    "portal_url": portal_url(slo),
                    "silence_url": silence_url(slo, name),
                },
            }
        )
    return rules


def _budget_rules(slo: AppSlo, kind: str) -> list[dict]:
    labels = _labels(slo, kind)
    sel = _selector(slo, kind)
    period = f"{slo.period_days}d"
    return [
        {
            "record": "slo:objective:ratio",
            "expr": f"vector({_num(slo.target(kind) / 100)})",
            "labels": labels,
        },
        {
            "record": "slo:error_budget:ratio",
            "expr": f"vector({_num(slo.budget(kind))})",
            "labels": labels,
        },
        {
            "record": "slo:time_period:days",
            "expr": f"vector({slo.period_days})",
            "labels": labels,
        },
        {
            "record": f"slo:sli_error:ratio_rate{period}",
            "expr": (
                f"sum_over_time(cnp_slo:errors:rate5m{{{sel}}}[{period}])"
                f" / sum_over_time(cnp_slo:requests:rate5m{{{sel}}}"
                f"[{period}])"
            ),
            "labels": labels,
        },
        {
            "record": "slo:period_error_budget_remaining:ratio",
            "expr": (
                f"1 - slo:sli_error:ratio_rate{period}{{{sel}}}"
                f" / {_num(slo.budget(kind))}"
            ),
            "labels": labels,
        },
    ]


def groups(slo: AppSlo) -> list[dict]:
    params = {"extra_filters": extra_filters(slo)}
    name = object_name(slo.project, slo.app)
    return [
        {
            "name": name,
            "interval": "1m",
            "params": params,
            "rules": [
                rule
                for kind in KINDS
                for rule in _recordings(slo, kind) + _alerts(slo, kind)
            ],
        },
        {
            # The period window reads weeks of samples: every 5 minutes is
            # plenty for a number shown on a dashboard.
            "name": f"{name}-budget",
            "interval": "5m",
            "params": params,
            "rules": [
                rule for kind in KINDS for rule in _budget_rules(slo, kind)
            ],
        },
    ]


def render(slo: AppSlo) -> dict:
    return {
        "apiVersion": "operator.victoriametrics.com/v1beta1",
        "kind": "VMRule",
        "metadata": {
            "name": object_name(slo.project, slo.app),
            "namespace": settings.OBS_RULES_NAMESPACE,
            "labels": {
                "cnp.3istor.com/owner": "cmp",
                "cnp.3istor.com/project": slo.project,
                "cnp.3istor.com/app": slo.app,
            },
        },
        "spec": {"groups": groups(slo)},
    }
