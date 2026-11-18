"""
What an import plan will hold and cost before it is applied (CNP-307).

Per app, the requests and limits the values files will carry, the volume
it claims and its monthly compute cost: requests priced at the rates of the
project's cluster, as the FinOps estimate prices measured usage (compute
only, no storage, network or platform fee). Then the project's import
quota, the free capacity of the cluster and, on a re-import, what changes
against the apps already deployed.
"""

import logging
import time
from datetime import date

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.imports import ImportRun
from app.models.project import Project, TargetCloud
from app.services import kube_client
from app.services.finops.estimate import HOURS_PER_MONTH
from app.services.finops.ingest import fx, inputs
from app.services.imports import generate, recommendations, service
from app.services.imports.generate import AppSpec, PlanError

logger = logging.getLogger(__name__)

# cnp-generic-app db.storage, CNPG sets no requests for the database pod.
DATABASE_VOLUME_GIB = 1
CLUSTER_CACHE_SECONDS = 60
# Fields of an app whose change a re-import shows.
DIFF_FIELDS = (
    "requests_mem_mib",
    "limits_mem_mib",
    "cpu",
    "public",
    "ports",
    "backing_kind",
    "database",
    "offhours",
)

_cluster_cache: dict[str, tuple[float, dict | None]] = {}


def _cpu_limit(spec: AppSpec) -> str:
    if spec.kind == "backing":
        preset = generate.load_preset(spec.backing_kind)
        return str(preset["resources"]["limits"]["cpu"])
    return "1" if spec.language in ("java", "kotlin", "scala") else "500m"


def _volume_gib(spec: AppSpec) -> float:
    gib = DATABASE_VOLUME_GIB if spec.database else 0
    if spec.kind == "backing":
        size = (
            generate.load_preset(spec.backing_kind)
            .get("workload", {})
            .get("persistence", {})
            .get("size")
        )
        if size:
            gib += generate.mib(size) / 1024
    return gib


def _managed_database_mib(plan: dict, spec: AppSpec) -> int:
    """What the analyzer guessed the database needs: an estimate only."""
    backing = next(
        (
            b
            for b in plan.get("backing") or []
            if b["name"] == (spec.database or {}).get("backing")
        ),
        None,
    )
    return int(backing["resources"]["requests_mem_mib"]) if backing else 0


def resolvable(plan: dict, edits: dict | None) -> tuple[list[AppSpec], list]:
    """
    The apps the plan deploys. A plan the API would refuse is estimated
    without the apps it refuses outright, with the problems alongside.
    """
    edits = dict(edits or {})
    try:
        return generate.resolve(plan, edits), []
    except PlanError as exc:
        problems = exc.problems
    unsupported = [
        b["name"]
        for b in plan.get("backing") or []
        if not generate.is_managed(b)
        and (b["kind"] not in generate.SUPPORTED_BACKING or not b["supported"])
    ]
    edits["exclude"] = sorted(
        set(edits.get("exclude") or []) | set(unsupported)
    )
    try:
        return generate.resolve(plan, edits), problems
    except PlanError as exc:
        return [], exc.problems


def _rate(
    cloud: TargetCloud, usd_per_eur: float | None
) -> inputs.NodeRates | None:
    """The FinOps rates of the cluster, None while FinOps has none."""
    try:
        return inputs.cluster_rates(usd_per_eur).get(cloud.value)
    except FileNotFoundError as exc:
        logger.info("Import estimate without prices: %s", exc)
        return None


def app_rows(
    plan: dict, specs: list[AppSpec], rate: inputs.NodeRates | None
) -> list[dict]:
    rows = []
    for spec in specs:
        cpu = generate.millicpu(spec.cpu)
        database_mib = _managed_database_mib(plan, spec)
        monthly = (
            round(
                rate.hourly_eur(
                    cpu / 1000,
                    (spec.requests_mem_mib + database_mib) / 1024,
                )
                * HOURS_PER_MONTH,
                2,
            )
            if rate
            else None
        )
        rows.append(
            {
                "app": spec.name,
                "kind": spec.kind,
                "requests": {
                    "cpu_m": cpu,
                    "mem_mib": spec.requests_mem_mib,
                },
                "limits": {
                    "cpu_m": generate.millicpu(_cpu_limit(spec)),
                    "mem_mib": spec.limits_mem_mib,
                },
                "database_mem_mib": database_mib or None,
                "volume_gib": _volume_gib(spec),
                "monthly_eur": monthly,
                # The catalogue presets are measured; a service's figures and
                # a managed database's RAM are the analyzer's guess.
                "estimated": spec.kind == "service" or bool(database_mib),
            }
        )
    return rows


def totals(rows: list[dict]) -> dict:
    priced = [r["monthly_eur"] for r in rows if r["monthly_eur"] is not None]
    return {
        "requests": {
            "cpu_m": sum(r["requests"]["cpu_m"] for r in rows),
            "mem_mib": sum(
                r["requests"]["mem_mib"] + (r["database_mem_mib"] or 0)
                for r in rows
            ),
        },
        "limits": {
            "cpu_m": sum(r["limits"]["cpu_m"] for r in rows),
            "mem_mib": sum(r["limits"]["mem_mib"] for r in rows),
        },
        "volume_gib": sum(r["volume_gib"] for r in rows),
        "monthly_eur": (
            round(sum(priced), 2) if len(priced) == len(rows) else None
        ),
    }


def quota(specs: list[AppSpec], other_imports_mib: int) -> dict:
    return {
        "project_max_mib": settings.IMPORT_MAX_PROJECT_REQUESTS_MIB,
        "namespace_max_mib": settings.IMPORT_NAMESPACE_REQUESTS_MIB,
        "namespace_max_millicpu": settings.IMPORT_NAMESPACE_REQUESTS_MILLICPU,
        "other_imports_mib": other_imports_mib,
        "problems": generate.check_capacity(
            specs,
            settings.IMPORT_MAX_PROJECT_REQUESTS_MIB,
            settings.IMPORT_NAMESPACE_REQUESTS_MIB,
            settings.IMPORT_NAMESPACE_REQUESTS_MILLICPU,
            other_imports_mib,
        ),
    }


def _quantity_millicpu(value: str) -> int:
    return generate.millicpu(value)


def _quantity_mib(value: str) -> int:
    value = str(value)
    if value.endswith("Ki"):
        return int(value[:-2]) // 1024
    if value.isdigit():
        return int(value) // (1024 * 1024)
    return generate.mib(value)


def _read_cluster(cloud: TargetCloud) -> dict:
    nodes = kube_client.kube_list("/api/v1/nodes", cloud=cloud)
    allocatable = {"cpu_m": 0, "mem_mib": 0}
    for node in nodes:
        spec = node.get("spec") or {}
        if spec.get("unschedulable"):
            continue
        status = (node.get("status") or {}).get("allocatable") or {}
        allocatable["cpu_m"] += _quantity_millicpu(status.get("cpu", "0"))
        allocatable["mem_mib"] += _quantity_mib(status.get("memory", "0Mi"))
    requested = {"cpu_m": 0, "mem_mib": 0}
    pods = kube_client.kube_list(
        "/api/v1/pods",
        {"fieldSelector": "status.phase!=Succeeded,status.phase!=Failed"},
        cloud=cloud,
    )
    for pod in pods:
        for container in (pod.get("spec") or {}).get("containers") or []:
            asked = (container.get("resources") or {}).get("requests") or {}
            requested["cpu_m"] += _quantity_millicpu(asked.get("cpu", "0"))
            requested["mem_mib"] += _quantity_mib(asked.get("memory", "0Mi"))
    return {
        "allocatable": allocatable,
        "requested": requested,
        "free": {
            key: allocatable[key] - requested[key] for key in allocatable
        },
    }


def cluster_capacity(cloud: TargetCloud) -> dict | None:
    """
    The schedulable room left on the project's cluster, by requests. None
    when the cluster cannot be read: the plan is then shown without it.
    """
    cached = _cluster_cache.get(cloud.value)
    if cached and time.monotonic() - cached[0] < CLUSTER_CACHE_SECONDS:
        return cached[1]
    try:
        found = _read_cluster(cloud)
    except (
        kube_client.KubeUnavailableError,
        kube_client.ClusterNotConnectedError,
    ) as exc:
        logger.info("Cluster capacity unavailable: %s", exc)
        found = None
    _cluster_cache[cloud.value] = (time.monotonic(), found)
    return found


def _comparable(spec: dict) -> dict:
    return {key: spec.get(key) for key in DIFF_FIELDS}


def diff(specs: list[AppSpec], deployed: dict[str, dict]) -> dict | None:
    """
    The plan against the apps of the import already deployed (name ->
    stored spec): None for a first import, empty lists when nothing changes.
    """
    if not deployed:
        return None
    wanted = {s.name: _comparable(s.to_dict()) for s in specs}
    changed = []
    for name in sorted(set(wanted) & set(deployed)):
        before = _comparable(deployed[name])
        for key in DIFF_FIELDS:
            if before[key] != wanted[name][key]:
                changed.append(
                    {
                        "app": name,
                        "field": key,
                        "before": before[key],
                        "after": wanted[name][key],
                    }
                )
    return {
        "added": sorted(set(wanted) - set(deployed)),
        "removed": sorted(set(deployed) - set(wanted)),
        "changed": changed,
    }


def recommendations_gain_mib(plan: dict, dismissed: set[str]) -> int:
    return sum(
        r.get("gain_mem_mib") or 0
        for r in plan.get("recommendations") or []
        if r.get("id") not in dismissed
    )


def estimate(  # pylint: disable=too-many-arguments
    plan: dict,
    edits: dict | None,
    cloud: TargetCloud,
    usd_per_eur: float | None,
    other_imports_mib: int,
    deployed: dict[str, dict],
    dismissed: set[str],
    capacity: dict | None,
) -> dict:
    specs, problems = resolvable(plan, edits)
    rate = _rate(cloud, usd_per_eur)
    rows = app_rows(plan, specs, rate)
    return {
        "basis": "requests",
        "cloud": cloud.value,
        "currency": "EUR",
        "priced": rate is not None,
        "rate_estimated": bool(rate and rate.estimated),
        "apps": rows,
        "total": totals(rows),
        "plan_problems": problems,
        "quota": quota(specs, other_imports_mib),
        "cluster": capacity,
        "diff": diff(specs, deployed),
        "recommendations_gain_mib": recommendations_gain_mib(plan, dismissed),
    }


def for_run(db: Session, run: ImportRun, edits: dict | None) -> dict:
    """The estimate of an analysis run's plan with *edits* applied."""
    source = run.source
    project = source.project_name
    row = db.query(Project).filter_by(project_name=project).first()
    cloud = TargetCloud((row.target_cloud if row else None) or "onprem")
    return estimate(
        run.plan or {},
        edits,
        cloud,
        fx.usd_per_eur(db, date.today()),
        service.other_imports_mib(db, project, source),
        {s.app_name: s.spec for s in source.services if s.deployment_id},
        recommendations.dismissed_ids(db, project),
        cluster_capacity(cloud),
    )
