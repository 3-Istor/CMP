"""
Recommendation engine — spots over-provisioning from quota vs simulated usage.

All four spec typologies are covered: replica reduction, CPU/RAM rightsizing,
prolonged inactivity, and oversized storage. Recommendations are **coherent and
non-destructive**: the suggested config is always ≤ the current one, and each
comes with a justification, an estimated monthly saving and a confidence score.

Recommendation ids are stable (``<app_id>:<type>``) so a user's ignore/apply
choice, persisted in the DB, keeps matching the recomputed recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from app.services.finops import usage
from app.services.finops.engine import AppSpec, Quota, projected_month_cost

# Thresholds (spec-driven).
_REPLICA_LOAD_THRESHOLD = 0.60  # avg load below which replicas can shrink
_RIGHTSIZE_PEAK_THRESHOLD = 0.50  # peak below which CPU/RAM limits can shrink
_STORAGE_USE_THRESHOLD = 0.40
_ANALYSIS_WINDOW_DAYS = 30


@dataclass
class Recommendation:
    id: str
    app_id: int
    app_name: str
    project_id: str | None
    rec_type: str  # replicas | rightsizing | inactivity | storage
    title: str
    justification: str
    current: dict  # human-readable current config
    recommended: dict  # human-readable recommended config
    monthly_saving_eur: float
    confidence: int  # 0..100
    effort: str  # low | medium | high
    # k8s patch to apply to deploy/values.yaml (None → cannot auto-apply)
    patch: dict | None = field(default=None)


def _avg_usage(app_id: int, resource: str, ref: date) -> float:
    total = 0.0
    for offset in range(_ANALYSIS_WINDOW_DAYS):
        total += usage.simulated_usage_pct(
            app_id, resource, ref - timedelta(days=offset)
        )
    return total / _ANALYSIS_WINDOW_DAYS


def _peak_usage(app_id: int, resource: str, ref: date) -> float:
    return max(
        usage.simulated_usage_pct(
            app_id, resource, ref - timedelta(days=offset)
        )
        for offset in range(_ANALYSIS_WINDOW_DAYS)
    )


def _monthly_for_quota(app: AppSpec, quota: Quota, ref: date) -> float:
    return projected_month_cost([replace(app, quota=quota)], ref)


def _saving(app: AppSpec, new_quota: Quota, ref: date) -> float:
    current = projected_month_cost([app], ref)
    proposed = _monthly_for_quota(app, new_quota, ref)
    return round(max(0.0, current - proposed), 2)


def recommendations_for_app(
    app: AppSpec, ref: date | None = None
) -> list[Recommendation]:
    ref = ref or date.today()
    recs: list[Recommendation] = []
    q = app.quota

    # ── 1. Replica optimisation ───────────────────────────────────────────
    cpu_avg = _avg_usage(app.id, "cpu", ref)
    ram_avg = _avg_usage(app.id, "ram", ref)
    load = max(cpu_avg, ram_avg)
    if q.replicas > 1 and load < _REPLICA_LOAD_THRESHOLD:
        # Keep enough replicas to sit around 70% load after shrinking.
        target = max(1, min(q.replicas, round(q.replicas * load / 0.70 + 0.5)))
        if target < q.replicas:
            new_q = replace(q, replicas=target)
            recs.append(
                Recommendation(
                    id=f"{app.id}:replicas",
                    app_id=app.id,
                    app_name=app.name,
                    project_id=app.project_id,
                    rec_type="replicas",
                    title="Réduire le nombre de replicas",
                    justification=(
                        f"La charge moyenne sur {_ANALYSIS_WINDOW_DAYS} jours est de "
                        f"{load * 100:.0f}% des capacités (< 60%)."
                    ),
                    current={"replicas": q.replicas},
                    recommended={"replicas": target},
                    monthly_saving_eur=_saving(app, new_q, ref),
                    confidence=_confidence(load, _REPLICA_LOAD_THRESHOLD),
                    effort="low",
                    patch={"replicaCount": target},
                )
            )

    # ── 2. Rightsizing CPU / RAM ──────────────────────────────────────────
    cpu_peak = _peak_usage(app.id, "cpu", ref)
    ram_peak = _peak_usage(app.id, "ram", ref)
    if (
        cpu_peak < _RIGHTSIZE_PEAK_THRESHOLD
        or ram_peak < _RIGHTSIZE_PEAK_THRESHOLD
    ):
        new_cpu = q.cpu_cores
        new_ram = q.ram_gb
        if cpu_peak < _RIGHTSIZE_PEAK_THRESHOLD:
            new_cpu = max(0.1, round(q.cpu_cores * (cpu_peak / 0.70 + 0.1), 2))
        if ram_peak < _RIGHTSIZE_PEAK_THRESHOLD:
            new_ram = max(0.1, round(q.ram_gb * (ram_peak / 0.70 + 0.1), 2))
        if new_cpu < q.cpu_cores or new_ram < q.ram_gb:
            new_q = replace(q, cpu_cores=new_cpu, ram_gb=new_ram)
            recs.append(
                Recommendation(
                    id=f"{app.id}:rightsizing",
                    app_id=app.id,
                    app_name=app.name,
                    project_id=app.project_id,
                    rec_type="rightsizing",
                    title="Ajuster les limites CPU / RAM",
                    justification=(
                        f"Les pics observés n'ont jamais dépassé "
                        f"{max(cpu_peak, ram_peak) * 100:.0f}% des quotas sur "
                        f"{_ANALYSIS_WINDOW_DAYS} jours."
                    ),
                    current={"cpu_cores": q.cpu_cores, "ram_gb": q.ram_gb},
                    recommended={"cpu_cores": new_cpu, "ram_gb": new_ram},
                    monthly_saving_eur=_saving(app, new_q, ref),
                    confidence=_confidence(
                        max(cpu_peak, ram_peak), _RIGHTSIZE_PEAK_THRESHOLD
                    ),
                    effort="medium",
                    patch={
                        "resources": {
                            "limits": {
                                "cpu": new_cpu,
                                "memory": f"{new_ram}Gi",
                            }
                        }
                    },
                )
            )

    # ── 3. Prolonged inactivity ───────────────────────────────────────────
    if usage.is_inactive(app.id, ref, window_days=7):
        recs.append(
            Recommendation(
                id=f"{app.id}:inactivity",
                app_id=app.id,
                app_name=app.name,
                project_id=app.project_id,
                rec_type="inactivity",
                title="Mettre en hibernation (inactivité)",
                justification="Aucun trafic réseau significatif depuis 7 jours.",
                current={"replicas": q.replicas, "status": "running"},
                recommended={"replicas": 0, "status": "hibernated"},
                monthly_saving_eur=_saving(app, replace(q, replicas=1), ref),
                confidence=80,
                effort="low",
                patch={"replicaCount": 0},
            )
        )

    # ── 4. Storage optimisation ───────────────────────────────────────────
    storage_avg = _avg_usage(app.id, "storage", ref)
    if storage_avg < _STORAGE_USE_THRESHOLD and q.storage_gb > 5:
        target = max(5.0, round(q.storage_gb * (storage_avg / 0.70 + 0.1)))
        if target < q.storage_gb:
            new_q = replace(q, storage_gb=target)
            recs.append(
                Recommendation(
                    id=f"{app.id}:storage",
                    app_id=app.id,
                    app_name=app.name,
                    project_id=app.project_id,
                    rec_type="storage",
                    title="Réduire le volume de stockage",
                    justification=(
                        f"Le stockage n'est utilisé qu'à {storage_avg * 100:.0f}% "
                        f"de sa capacité provisionnée."
                    ),
                    current={"storage_gb": q.storage_gb},
                    recommended={"storage_gb": target},
                    monthly_saving_eur=_saving(app, new_q, ref),
                    confidence=_confidence(
                        storage_avg, _STORAGE_USE_THRESHOLD
                    ),
                    effort="high",  # PV resize is disruptive
                    patch=None,
                )
            )

    return [
        r
        for r in recs
        if r.monthly_saving_eur > 0 or r.rec_type == "inactivity"
    ]


def recommendations_for_apps(
    apps: list[AppSpec], ref: date | None = None
) -> list[Recommendation]:
    out: list[Recommendation] = []
    for app in apps:
        out.extend(recommendations_for_app(app, ref))
    out.sort(key=lambda r: r.monthly_saving_eur, reverse=True)
    return out


def _confidence(observed: float, threshold: float) -> int:
    """
    The further below the threshold the observation sits, the more confident we
    are that the resource is over-provisioned. Clamped to a 60..98 range.
    """
    if threshold <= 0:
        return 80
    ratio = observed / threshold  # 0 → way under, 1 → at threshold
    conf = 98 - ratio * 38  # 60..98
    return int(max(60, min(98, conf)))
