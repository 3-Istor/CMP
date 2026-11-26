"""
Redistribute what a cloud invoice charges for a Kubernetes cluster's nodes
over the projects that ran on it, using the OpenCost allocation of the day.

The invoice only says "these instances cost X"; OpenCost says who used them.
Node lines are ingested under the project ``__cluster__``. For each cloud and
day this step writes ``<cloud>_split`` lines (one per namespace, idle included
under ``__idle__``) whose amounts add up exactly to the node cost.

No double counting: node lines are never modified in their amounts. The rule
for every total is that ``__cluster__`` lines of a (cloud, day) that has split
lines are ignored (see :func:`split_days`). Without OpenCost data for the day
the split falls back to which projects were on the cloud (see ``presence``);
with neither, the node lines stay counted as unallocated.
"""

import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, time

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models.deployment import Deployment, DeploymentStatus
from app.models.finops import CostLineItem, MonthClose
from app.models.project import Project
from app.models.project_move import ProjectMove
from app.services.finops.ingest import presence
from app.services.finops.ingest.focus import CLUSTER
from app.services.finops.ingest.projects import owner_of
from app.services.finops.ingest.store import CostLine, upsert

logger = logging.getLogger(__name__)

SPLIT_TAG = "x_split_into"
EKS_SERVICE = "Amazon Elastic Container Service for Kubernetes"
GKE_CLUSTER = "cnp-gke"


def invoice_source(cloud: str) -> str:
    return f"{cloud}_focus"


def split_source(cloud: str) -> str:
    return f"{cloud}_split"


def split_days(db: Session, cloud: str, billing_period: str) -> set[datetime]:
    """Days whose ``__cluster__`` node lines are replaced by split lines."""
    return set(
        db.scalars(
            select(CostLineItem.charge_period_start)
            .where(
                CostLineItem.x_source == split_source(cloud),
                CostLineItem.billing_period == billing_period,
            )
            .distinct()
        )
    )


def _shares(
    db: Session, cloud: str, start: datetime
) -> dict[tuple[str, str | None, str], float]:
    costs: dict[tuple[str, str | None, str], float] = defaultdict(float)
    rows = db.execute(
        select(
            CostLineItem.project,
            CostLineItem.app,
            CostLineItem.resource_name,
            CostLineItem.x_cost_eur,
        ).where(
            CostLineItem.x_source == "opencost",
            CostLineItem.cloud == cloud,
            CostLineItem.charge_period_start == start,
            CostLineItem.project.is_not(None),
        )
    )
    for project, app, namespace, cost in rows:
        if cost and cost > 0:
            costs[(project, app, namespace)] += cost
    return dict(costs)


def _cluster_hours(db: Session, cloud: str, start: datetime) -> int | None:
    """Hours the cluster ran that day, from its control-plane fee line."""
    rows = db.execute(
        select(
            CostLineItem.consumed_quantity, CostLineItem.consumed_unit
        ).where(
            CostLineItem.x_source == invoice_source(cloud),
            CostLineItem.charge_period_start == start,
            or_(
                CostLineItem.service_name == EKS_SERVICE,
                CostLineItem.resource_name == GKE_CLUSTER,
            ),
        )
    )
    hours = [
        q for q, unit in rows if q and unit and unit.lower().startswith("h")
    ]
    return min(math.ceil(max(hours)), 24) if hours else None


def _presence_shares(
    db: Session, cloud: str, start: datetime, total: float
) -> dict[tuple[str, str | None, str], float]:
    projects = [
        (name, target.value, created)
        for name, target, created in db.execute(
            select(
                Project.project_name, Project.target_cloud, Project.created_at
            )
        )
    ]
    moves = [
        tuple(row)
        for row in db.execute(
            select(
                ProjectMove.project,
                ProjectMove.source,
                ProjectMove.target,
                ProjectMove.status,
                ProjectMove.created_at,
                ProjectMove.finished_at,
            )
        )
    ]
    present = presence.hours_present(
        presence.stays(projects, moves), cloud, start.date()
    )
    if not present:
        return {}
    namespaces = {p: {f"{p}-system": None} for p in present}
    for project, namespace in db.execute(
        select(Deployment.project_id, Deployment.k8s_namespace).where(
            Deployment.project_id.in_(present),
            Deployment.k8s_namespace.is_not(None),
            Deployment.k8s_namespace != "",
            # Deleted rows keep their namespace.
            Deployment.status != DeploymentStatus.DELETED,
        )
    ):
        namespaces[project][namespace] = owner_of(
            namespace, {namespace: project}
        ).app
    apps = {
        p: [(app, ns) for ns, app in by_ns.items()]
        for p, by_ns in namespaces.items()
    }
    return presence.shares(
        total, _cluster_hours(db, cloud, start), present, apps
    )


def split_day(db: Session, cloud: str, day: date) -> int:
    """Split one day; returns the number of split lines written."""
    start = datetime.combine(day, time.min)
    period = start.strftime("%Y-%m")
    if db.scalar(
        select(MonthClose.id).where(MonthClose.billing_period == period)
    ):
        return 0
    nodes = db.scalars(
        select(CostLineItem).where(
            CostLineItem.x_source == invoice_source(cloud),
            CostLineItem.project == CLUSTER,
            CostLineItem.charge_period_start == start,
        )
    ).all()
    total = sum(n.x_cost_eur for n in nodes)
    costs = _shares(db, cloud, start)
    method = "opencost"
    if nodes and total > 0 and not costs:
        costs = _presence_shares(db, cloud, start, total)
        method = "presence"
    usage = sum(costs.values())

    db.execute(
        delete(CostLineItem).where(
            CostLineItem.x_source == split_source(cloud),
            CostLineItem.charge_period_start == start,
        )
    )
    splittable = bool(nodes) and total > 0 and usage > 0
    for node in nodes:
        tags = {k: v for k, v in node.tags.items() if k != SPLIT_TAG}
        if splittable:
            tags[SPLIT_TAG] = split_source(cloud)
        node.tags = tags
    if not splittable:
        db.commit()
        return 0

    final = all(n.x_is_final for n in nodes)
    estimate = method == "presence" or any(n.x_is_estimate for n in nodes)
    lines: list[CostLine] = []
    allocated = 0.0
    ordered = sorted(costs.items(), key=lambda item: item[0][2])
    for index, ((project, app, namespace), cost) in enumerate(ordered):
        if index == len(ordered) - 1:
            share = total - allocated
        else:
            share = total * cost / usage
        allocated += share
        lines.append(
            CostLine(
                x_source=split_source(cloud),
                provider_name=nodes[0].provider_name,
                service_name="Kubernetes cluster nodes",
                service_category="Compute",
                charge_period_start=start,
                charge_period_end=nodes[0].charge_period_end,
                resource_id=f"{cloud}-split/{namespace}",
                resource_name=namespace,
                x_cost_eur=share,
                billed_cost=share,
                list_cost=share,
                effective_cost=share,
                project=project,
                app=app,
                cloud=cloud,
                x_allocation_method=method,
                x_pricing_basis="invoice",
                x_is_final=final,
                x_is_estimate=estimate,
                extra_tags={
                    "k8s_namespace": namespace,
                    "k8s_cluster": ",".join(
                        sorted({n.tags.get("k8s_cluster", "") for n in nodes})
                    ),
                },
            )
        )
    return upsert(db, lines)


def split_cloud(db: Session, cloud: str, days: Iterable[date]) -> int:
    written = 0
    for day in sorted(set(days)):
        written += split_day(db, cloud, day)
    return written
