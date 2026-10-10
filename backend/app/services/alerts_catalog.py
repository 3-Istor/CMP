"""
Ready-made alerts a project or an app can switch on. Each one is a PromQL
rule on VictoriaMetrics, provisioned in the project's Grafana org; people who
need anything else write their own in Grafana.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    unit: str
    default: int
    minimum: int
    maximum: int


@dataclass(frozen=True)
class AlertTemplate:
    id: str
    title: str
    description: str
    severity: str
    wait: str
    params: tuple[Param, ...]
    # PromQL from the namespace matcher and the parameter values.
    expr: Callable[[str, dict[str, int]], str]

    def defaults(self) -> dict[str, int]:
        return {p.key: p.default for p in self.params}


_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def namespace_matcher(namespaces: list[str]) -> str:
    """
    Exact names, so a project called "shop" never matches "shopping-web".
    PromQL anchors the regex; namespace names hold no regex metacharacters,
    and escaping "-" is not valid in a PromQL string.
    """
    names = sorted(namespaces)
    if not names or not all(_NAMESPACE.match(n) for n in names):
        raise ValueError(f"Invalid namespaces: {names}")
    return 'namespace=~"' + "|".join(names) + '"'


CATALOG: tuple[AlertTemplate, ...] = (
    AlertTemplate(
        id="app_unavailable",
        title="Application indisponible",
        description="Moins de réplicas prêts que demandés pendant plusieurs minutes.",
        severity="critical",
        wait="5m",
        params=(),
        expr=lambda ns, p: (
            f"sum by (namespace, deployment) (kube_deployment_spec_replicas{{{ns}}})"
            f" - sum by (namespace, deployment) "
            f"(kube_deployment_status_replicas_available{{{ns}}}) > 0"
        ),
    ),
    AlertTemplate(
        id="restarts",
        title="Redémarrages en boucle",
        description="Un conteneur redémarre plusieurs fois en 15 minutes (CrashLoop).",
        severity="critical",
        wait="1m",
        params=(Param("restarts", "Redémarrages en 15 min", "", 3, 1, 50),),
        expr=lambda ns, p: (
            f"sum by (namespace, pod, container) "
            f"(increase(kube_pod_container_status_restarts_total{{{ns}}}[15m]))"
            f" > {p['restarts']}"
        ),
    ),
    AlertTemplate(
        id="oom_killed",
        title="Conteneur tué par manque de mémoire",
        description="Un conteneur a redémarré après un OOMKilled.",
        severity="warning",
        wait="0s",
        params=(),
        expr=lambda ns, p: (
            f"sum by (namespace, pod, container) "
            f"(increase(kube_pod_container_status_restarts_total{{{ns}}}[10m])) > 0"
            f" and on (namespace, pod, container) max by (namespace, pod, container) "
            f'(kube_pod_container_status_last_terminated_reason{{{ns}, reason="OOMKilled"}}) > 0'
        ),
    ),
    AlertTemplate(
        id="memory_near_limit",
        title="Mémoire proche de la limite",
        description="Un conteneur utilise presque toute sa mémoire autorisée : un OOMKilled approche.",
        severity="warning",
        wait="10m",
        params=(Param("percent", "Seuil", "%", 90, 50, 99),),
        expr=lambda ns, p: (
            f'max by (namespace, pod, container) (container_memory_working_set_bytes{{{ns}, container!=""}})'
            f" / on (namespace, pod, container) max by (namespace, pod, container) "
            f'(kube_pod_container_resource_limits{{{ns}, resource="memory"}}) * 100'
            f" > {p['percent']}"
        ),
    ),
    AlertTemplate(
        id="cpu_throttled",
        title="CPU bridé",
        description="Un conteneur passe une grande part du temps bridé par sa limite CPU : il ralentit.",
        severity="warning",
        wait="15m",
        params=(Param("percent", "Part du temps bridé", "%", 50, 10, 100),),
        expr=lambda ns, p: (
            f'sum by (namespace, pod, container) (rate(container_cpu_cfs_throttled_periods_total{{{ns}, container!=""}}[5m]))'
            f' / sum by (namespace, pod, container) (rate(container_cpu_cfs_periods_total{{{ns}, container!=""}}[5m])) * 100'
            f" > {p['percent']}"
        ),
    ),
    AlertTemplate(
        id="disk_almost_full",
        title="Volume presque plein",
        description="Un volume persistant (base de données comprise) est presque plein.",
        severity="warning",
        wait="10m",
        params=(Param("percent", "Seuil", "%", 85, 50, 99),),
        expr=lambda ns, p: (
            f"max by (namespace, persistentvolumeclaim) "
            f"(kubelet_volume_stats_used_bytes{{{ns}}} / kubelet_volume_stats_capacity_bytes{{{ns}}}) * 100"
            f" > {p['percent']}"
        ),
    ),
    AlertTemplate(
        id="error_logs",
        title="Pic d'erreurs dans les logs",
        description="Beaucoup de lignes d'erreur (error, fatal, panic, exception) en 5 minutes.",
        severity="warning",
        wait="5m",
        params=(
            Param("lines", "Lignes d'erreur en 5 min", "", 20, 1, 100000),
        ),
        expr=lambda ns, p: (
            f"sum by (namespace) (increase(loki_process_custom_log_error_lines_total{{{ns}}}[5m]))"
            f" > {p['lines']}"
        ),
    ),
    AlertTemplate(
        id="log_volume",
        title="Volume de logs anormal",
        description="Une app écrit énormément de logs en 10 minutes, souvent une boucle d'erreur.",
        severity="warning",
        wait="10m",
        params=(Param("lines", "Lignes en 10 min", "", 30000, 100, 10000000),),
        expr=lambda ns, p: (
            f"sum by (namespace) (increase(loki_process_custom_log_lines_total{{{ns}}}[10m]))"
            f" > {p['lines']}"
        ),
    ),
)

BY_ID = {t.id: t for t in CATALOG}
