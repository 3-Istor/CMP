"""
Daily cost anomalies per application, with their probable cause.

A day is flagged when the app costs more than the median of its previous
14 days by ``FINOPS_ANOMALY_PCT`` and by ``FINOPS_ANOMALY_MIN_EUR``. The
median ignores an earlier spike, so one anomaly does not hide the next. The
cause is looked up among the deployments of the app on that day or the day
before: Argo CD syncs, rollouts and configuration changes made in the CMP.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit import ActivityRecord, AuditEvent
from app.models.deployment import Deployment
from app.models.finops import CostAlert
from app.models.release import ReleaseEvent
from app.services.finops.forecast import daily_totals

BASELINE_DAYS = 14
MIN_BASELINE_DAYS = 5
NO_CAUSE = "aucun événement de déploiement"
_SIZING = re.compile(r"replica|resources|cpu|memory|autoscal", re.I)
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Anomaly:
    day: date
    cost_eur: float
    baseline_eur: float


def detect(
    daily: dict[date, float],
    since: date,
    pct: float | None = None,
    min_eur: float | None = None,
) -> list[Anomaly]:
    """Days on or after ``since`` whose cost jumps above the 14-day median."""
    pct = settings.FINOPS_ANOMALY_PCT if pct is None else pct
    min_eur = settings.FINOPS_ANOMALY_MIN_EUR if min_eur is None else min_eur
    found = []
    for day in sorted(d for d in daily if d >= since):
        previous = [
            v
            for d, v in daily.items()
            if day - timedelta(days=BASELINE_DAYS) <= d < day
        ]
        if len(previous) < MIN_BASELINE_DAYS:
            continue
        baseline = statistics.median(previous)
        cost = daily[day]
        if cost > baseline * (1 + pct) and cost - baseline > min_eur:
            found.append(Anomaly(day, cost, baseline))
    return found


def app_daily(provider, app, start: date, end: date) -> dict[date, float]:
    """
    Daily cost of the app's own namespace. Project-level lines (AI gateway,
    cloud invoices) are shared by every app of the project, so a jump there
    says nothing about this app.
    """
    own = replace(app, project_id=None)
    return daily_totals(provider.timeline([own], start, end, "daily"))


def _short(revision: str) -> str:
    return revision[:7] if _SHA.match(revision or "") else revision


def _details(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _window(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day - timedelta(days=1), time.min, timezone.utc),
        datetime.combine(day + timedelta(days=1), time.min, timezone.utc),
    )


def _argocd_syncs(db: Session, deployment: Deployment, day: date) -> list:
    start, end = _window(day)
    same_app = [ActivityRecord.app == deployment.name]
    if deployment.argocd_app_name:
        same_app.append(ActivityRecord.target == deployment.argocd_app_name)
    rows = db.scalars(
        select(ActivityRecord).where(
            ActivityRecord.action == "deployment.sync",
            ActivityRecord.project == deployment.project_id,
            or_(*same_app),
            ActivityRecord.time >= start,
            ActivityRecord.time < end,
        )
    ).all()
    events = []
    for row in rows:
        revisions = [
            _short(str(r))
            for r in (_details(row.details).get("revisions") or {}).values()
            if r
        ]
        by = "" if row.actor == "Argo CD" else f" par {row.actor}"
        events.append(
            {
                "kind": "argocd_sync",
                "time": row.time,
                "actor": row.actor,
                "revision": revisions[0] if revisions else None,
                "summary": (
                    f"Sync Argo CD {row.target}"
                    + (f" ({', '.join(revisions)})" if revisions else "")
                    + by
                ),
                "changes": [],
            }
        )
    return events


def _releases(db: Session, deployment: Deployment, day: date) -> list:
    start, end = _window(day)
    rows = db.scalars(
        select(ReleaseEvent).where(
            ReleaseEvent.deployment_id == deployment.id,
            ReleaseEvent.started_at >= start,
            ReleaseEvent.started_at < end,
        )
    ).all()
    events = []
    for row in rows:
        tag = row.image.rsplit(":", 1)[-1] if row.image else None
        events.append(
            {
                "kind": "release",
                "time": row.started_at,
                "actor": ", ".join(row.actors or []) or None,
                "revision": tag,
                "summary": (
                    f"Release r{row.revision} ({row.strategy}, {row.outcome})"
                    + (f" image {tag}" if tag else "")
                ),
                "changes": [],
            }
        )
    return events


def _config_changes(db: Session, deployment: Deployment, day: date) -> list:
    start, end = _window(day)
    rows = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "app.config.update",
            AuditEvent.outcome == "success",
            AuditEvent.project == deployment.project_id,
            AuditEvent.app == deployment.name,
            AuditEvent.created_at >= start,
            AuditEvent.created_at < end,
        )
    ).all()
    events = []
    for row in rows:
        sizing = [
            c
            for c in _details(row.details).get("changes") or []
            if isinstance(c, dict) and _SIZING.search(str(c.get("path", "")))
        ]
        described = ", ".join(
            f"{c['path']} {c.get('before')} → {c.get('after')}" for c in sizing
        )
        events.append(
            {
                "kind": "config_change",
                "time": row.created_at,
                "actor": row.actor,
                "revision": None,
                "summary": (
                    f"Config modifiée par {row.actor}"
                    + (f" : {described}" if described else "")
                ),
                "changes": sizing,
            }
        )
    return events


def deployment_events(
    db: Session, deployment: Deployment, day: date
) -> list[dict]:
    """Deployments of the app between the start of ``day - 1`` and ``day``."""
    events = (
        _argocd_syncs(db, deployment, day)
        + _releases(db, deployment, day)
        + _config_changes(db, deployment, day)
    )
    return sorted(events, key=lambda e: str(e["time"]))


def find_anomalies(
    db: Session,
    provider,
    deployments: list[Deployment],
    start: date,
    end: date,
) -> list[dict]:
    """Anomalies of each deployment between ``start`` and ``end``."""
    rows = []
    for deployment in deployments:
        app = provider.specs([deployment])[0]
        daily = app_daily(
            provider, app, start - timedelta(days=BASELINE_DAYS), end
        )
        for anomaly in detect(daily, start):
            events = deployment_events(db, deployment, anomaly.day)
            delta = anomaly.cost_eur - anomaly.baseline_eur
            rows.append(
                {
                    "app_id": int(deployment.id),
                    "app_name": deployment.name,
                    "project_id": deployment.project_id,
                    "date": anomaly.day,
                    "cost_eur": round(anomaly.cost_eur, 4),
                    "baseline_eur": round(anomaly.baseline_eur, 4),
                    "delta_eur": round(delta, 4),
                    "delta_pct": (
                        round(delta / anomaly.baseline_eur * 100, 1)
                        if anomaly.baseline_eur > 0
                        else None
                    ),
                    "cause": "; ".join(e["summary"] for e in events)
                    or NO_CAUSE,
                    "events": events,
                }
            )
    rows.sort(key=lambda r: (r["date"], r["app_name"]), reverse=True)
    return rows


def record_alerts(db: Session, anomalies: list[dict]) -> list[dict]:
    """Store each anomaly once as a spike alert; return the new ones."""
    new = []
    for row in anomalies:
        known = db.scalar(
            select(CostAlert.id).where(
                CostAlert.kind == "spike",
                CostAlert.app_id == row["app_id"],
                CostAlert.subject_day == row["date"],
            )
        )
        if known:
            continue
        pct = f" (+{row['delta_pct']:.0f} %)" if row["delta_pct"] else ""
        message = (
            f"Coût de {row['app_name']} le {row['date']:%d/%m} : "
            f"{row['cost_eur']:.2f} € contre {row['baseline_eur']:.2f} € "
            f"(médiane 14 j){pct}. Cause probable : {row['cause']}"
        )
        db.add(
            CostAlert(
                project_name=row["project_id"],
                app_id=row["app_id"],
                level="warning",
                kind="spike",
                message=message[:500],
                value_pct=row["delta_pct"],
                subject_day=row["date"],
            )
        )
        new.append(row)
    db.commit()
    return new
