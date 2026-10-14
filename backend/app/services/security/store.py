"""
Persistence of findings, scan states, exceptions and daily snapshots.

Each collection pass owns a set of sources and a set of apps: it may only
resolve the findings of those. An app whose data could not be read keeps its
findings open rather than reading as fixed.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.security import (
    SecurityException,
    SecurityFinding,
    SecurityScan,
    SecuritySnapshot,
)
from app.services.security import scoring
from app.services.security.model import (
    TIER_RANK,
    Audience,
    Category,
    Draft,
    Source,
    Tier,
)

ALL_APPS = None


def upsert_findings(
    db: Session,
    project: str,
    drafts: list[Draft],
    sources: tuple[Source, ...],
    apps: set[str | None] | None,
    now: datetime,
) -> list[SecurityFinding]:
    """
    Store what *sources* saw on *apps* (ALL_APPS: every app of the project,
    and the project itself) and resolve what they no longer see.

    Returns the findings that are new or reopened.
    """
    source_values = [s.value for s in sources]
    existing = {
        (f.fingerprint, f.source): f
        for f in db.query(SecurityFinding).filter(
            SecurityFinding.project == project,
            SecurityFinding.source.in_(source_values),
        )
    }
    seen = set()
    fresh = []
    for draft in drafts:
        key = (draft.fingerprint, draft.source.value)
        if key in seen:
            continue
        seen.add(key)
        values = draft.model_dump(exclude={"fingerprint", "source"})
        values.update(
            category=draft.category.value,
            tier=draft.tier.value,
            audience=draft.audience.value,
        )
        row = existing.get(key)
        if row is None:
            row = SecurityFinding(
                project=project,
                fingerprint=draft.fingerprint,
                source=draft.source.value,
                first_seen=now,
                **values,
            )
            db.add(row)
            fresh.append(row)
        else:
            if row.resolved_at is not None:
                row.resolved_at = None
                row.first_seen = now
                fresh.append(row)
            for name, value in values.items():
                setattr(row, name, value)
        row.last_seen = now

    for key, row in existing.items():
        if key in seen or row.resolved_at is not None:
            continue
        if apps is not ALL_APPS and row.app not in apps:
            continue
        row.resolved_at = now
    db.commit()
    db.flush()
    return fresh


def scan_state(
    db: Session, project: str, app: str | None, source: Source
) -> SecurityScan:
    row = (
        db.query(SecurityScan)
        .filter_by(project=project, app=app or "", source=source.value)
        .one_or_none()
    )
    if row is None:
        row = SecurityScan(project=project, app=app or "", source=source.value)
        db.add(row)
        # SessionLocal does not autoflush: without this, the next lookup in
        # the same pass misses the row and inserts a duplicate.
        db.flush()
    return row


def scans(db: Session, project: str, app: str | None) -> list[SecurityScan]:
    query = db.query(SecurityScan).filter(SecurityScan.project == project)
    if app is not None:
        query = query.filter(SecurityScan.app.in_([app, ""]))
    return query.all()


# ── Reading ──────────────────────────────────────────────────────────────────


@dataclass
class Merged:
    """One problem as the dashboard shows it: all sources merged."""

    fingerprint: str
    app: str | None
    category: Category
    tier: Tier
    audience: Audience
    rule: str
    title: str
    detail: str
    fix: str
    location: str
    link: str | None
    raw: str
    sources: list[str]
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None
    finding_ids: list[int] = field(default_factory=list)
    exception: SecurityException | None = None

    @property
    def excepted(self) -> bool:
        return self.exception is not None

    def scored(self) -> scoring.Scored:
        return scoring.Scored(
            self.app, self.category, self.tier, self.excepted
        )


def exception_applies(
    exception: SecurityException, tier: Tier, today: date
) -> bool:
    """
    Expired and revoked exceptions do nothing. Core findings and accepted
    risks need a project admin's approval first.
    """
    if exception.revoked_at is not None or exception.expires_on < today:
        return False
    needs_approval = tier is Tier.CORE or exception.status == "accepted_risk"
    return exception.approved_at is not None or not needs_approval


def active_exceptions(
    db: Session, project: str
) -> dict[str, SecurityException]:
    rows = (
        db.query(SecurityException)
        .filter(
            SecurityException.project == project,
            SecurityException.revoked_at.is_(None),
        )
        .order_by(SecurityException.created_at)
        .all()
    )
    return {row.fingerprint: row for row in rows}


def merged_findings(
    db: Session,
    project: str,
    today: date,
    app: str | None = None,
    include_resolved: bool = False,
) -> list[Merged]:
    query = db.query(SecurityFinding).filter(
        SecurityFinding.project == project
    )
    if app is not None:
        query = query.filter(SecurityFinding.app == app)
    if not include_resolved:
        query = query.filter(SecurityFinding.resolved_at.is_(None))

    groups: dict[str, list[SecurityFinding]] = defaultdict(list)
    for row in query:
        groups[row.fingerprint].append(row)

    exceptions = active_exceptions(db, project)
    merged = []
    for rows in groups.values():
        lead = max(rows, key=lambda r: TIER_RANK[Tier(r.tier)])
        open_rows = [r for r in rows if r.resolved_at is None]
        item = Merged(
            fingerprint=lead.fingerprint,
            app=lead.app,
            category=Category(lead.category),
            tier=Tier(lead.tier),
            audience=Audience(lead.audience),
            rule=lead.rule,
            title=lead.title,
            detail=lead.detail,
            fix=lead.fix,
            location=lead.location,
            link=lead.link,
            raw=lead.raw,
            sources=sorted({r.source for r in rows}),
            first_seen=min(r.first_seen for r in rows),
            last_seen=max(r.last_seen for r in rows),
            resolved_at=(
                None
                if open_rows
                else max(r.resolved_at for r in rows if r.resolved_at)
            ),
            finding_ids=[r.id for r in rows],
        )
        exception = exceptions.get(item.fingerprint)
        if exception and exception_applies(exception, item.tier, today):
            item.exception = exception
        merged.append(item)
    return merged


def for_audience(items: list[Merged], platform: bool) -> list[Merged]:
    wanted = Audience.PLATFORM if platform else Audience.DEVELOPER
    return [m for m in items if m.audience is wanted]


# ── Snapshots ────────────────────────────────────────────────────────────────


def write_snapshots(
    db: Session, project: str, apps: list[str], today: date
) -> None:
    """Upsert today's developer-view score of each app and of the project."""
    items = for_audience(merged_findings(db, project, today), platform=False)
    since = datetime.combine(today, datetime.min.time())

    def upsert(app: str, subset: list[Merged], value: int) -> None:
        open_ = scoring.actionable([m.scored() for m in subset])
        row = (
            db.query(SecuritySnapshot)
            .filter_by(day=today, project=project, app=app)
            .one_or_none()
        )
        if row is None:
            row = SecuritySnapshot(day=today, project=project, app=app)
            db.add(row)
        row.score = value
        row.grade = scoring.grade(
            value, scoring.has_core([m.scored() for m in subset])
        )
        row.core = sum(1 for f in open_ if f.tier is Tier.CORE)
        row.important = sum(1 for f in open_ if f.tier is Tier.IMPORTANT)
        row.recommended = sum(1 for f in open_ if f.tier is Tier.RECOMMENDED)
        row.new_major = sum(
            1
            for m in subset
            if m.first_seen >= since
            and not m.excepted
            and m.tier in (Tier.CORE, Tier.IMPORTANT)
        )

    app_scores = []
    for app in apps:
        subset = [m for m in items if m.app == app]
        value = scoring.score([m.scored() for m in subset])
        app_scores.append(value)
        upsert(app, subset, value)
    project_wide = [m for m in items if m.app is None]
    upsert(
        "",
        items,
        scoring.project_score(app_scores, [m.scored() for m in project_wide]),
    )
    db.commit()


def snapshots(
    db: Session, project: str, app: str | None, days: int, today: date
) -> list[SecuritySnapshot]:
    return (
        db.query(SecuritySnapshot)
        .filter(
            SecuritySnapshot.project == project,
            SecuritySnapshot.app == (app or ""),
            SecuritySnapshot.day > today - timedelta(days=days),
        )
        .order_by(SecuritySnapshot.day)
        .all()
    )
