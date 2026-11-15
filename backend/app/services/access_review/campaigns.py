"""
Life of an access review campaign.

    start --> open --(deadline, items undecided)--> escalated --> closed
                \\-------------------------------------------------^

At the start every project member (the owner aside, who cannot be removed)
and every member of the platform groups becomes an item. A project item is
decided by the project owner, or by its admins when the owner is gone, or by
infra when nobody is left. That list is frozen at the start, but the
authority behind it is checked again on every decision, so someone demoted
since then no longer decides. A platform item is decided by an infra member
other than the person concerned. Nobody decides on their own access.

Project decisions apply at once through the CMP membership code. Platform
groups are declared in the K3s Terraform, so removing someone there turns
into a task naming the line to change. The deadline only escalates: nothing
is ever removed without a decision.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.roles import ASSIGNABLE, ProjectRole
from app.models.access_review import AccessReviewCampaign, AccessReviewItem
from app.models.project import Project, ProjectStatus
from app.services import keycloak_service
from app.services.access_review import accounts, directory
from app.services.access_review.notices import Inbox, Notice
from app.services.audit import record_system_event
from app.services.authz import (
    AUDITOR_GROUP,
    FINOPS_GROUP,
    PLATFORM_ADMIN_GROUP,
)

logger = logging.getLogger(__name__)

OPEN, CLOSED = "open", "closed"
PROJECT, PLATFORM = "project", "platform"
DECISIONS = ("keep", "remove", "change_role")
PLATFORM_GROUPS = (PLATFORM_ADMIN_GROUP, AUDITOR_GROUP, FINOPS_GROUP)
REVIEWED_STATUSES = (ProjectStatus.ACTIVE, ProjectStatus.SUSPENDED)
MAX_DEADLINE_DAYS = 90
NOTE_MAX = 500
LINK = "/access-reviews"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ReviewError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


def reviewers_of(item: AccessReviewItem) -> list[str]:
    return json.loads(item.reviewers or "[]")


def terraform_hint(username: str, group: str) -> str:
    """Where the K3s Terraform declares *username* in *group*."""
    if group == PLATFORM_ADMIN_GROUP:
        where = (
            "local.platform_admins (it also feeds k3s-admin), "
            'resource keycloak_group_memberships.platform["infra"]'
        )
    else:
        where = (
            f'local.platform_group_members["{group}"], '
            f'resource keycloak_group_memberships.platform["{group}"]'
        )
    return (
        f'K3s terraform/groups.tf: remove "{username}" from {where}, '
        "then plan and apply."
    )


def _project_link(project: str) -> str:
    return f"/projects/{project}"


# ── Start ────────────────────────────────────────────────────────────────────


def _last_logins(db: Session, now: datetime) -> tuple[dict, set | None]:
    """Last login per username, and the enabled usernames (None: unknown)."""
    try:
        realm_users, rows = accounts.refresh_logins(db, now)
    except directory.DirectoryError as exc:
        logger.warning("Last logins unknown for the review: %s", exc)
        return {}, None
    logins = {
        r.username: r.last_login_at for r in rows.values() if r.last_login_at
    }
    enabled = {
        u["username"]
        for u in realm_users
        if u.get("enabled", True) and u.get("username")
    }
    return logins, enabled


def _project_items(
    project: Project,
    infra: list[str],
    logins: dict,
    enabled: set | None,
) -> list[AccessReviewItem]:
    members = keycloak_service.list_project_members(project.project_name)

    def present(name: str) -> bool:
        return enabled is None or name in enabled

    owner = project.owner_username
    if owner and present(owner):
        reviewers = [owner]
    else:
        reviewers = sorted(
            m["username"]
            for m in members
            if m["role"] == ProjectRole.ADMIN.value and present(m["username"])
        ) or list(infra)
    return [
        AccessReviewItem(
            scope=PROJECT,
            subject=project.project_name,
            username=m["username"],
            role=m["role"],
            last_login_at=logins.get(m["username"]),
            reviewers=json.dumps([r for r in reviewers if r != m["username"]]),
        )
        for m in members
        if m["username"] != owner
    ]


def _platform_items(
    group: str, infra: list[str], logins: dict
) -> list[AccessReviewItem]:
    return [
        AccessReviewItem(
            scope=PLATFORM,
            subject=group,
            username=name,
            role="member",
            last_login_at=logins.get(name),
            reviewers=json.dumps([r for r in infra if r != name]),
        )
        for name in directory.group_members(group)
    ]


def _opened_notices(
    campaign: AccessReviewCampaign, items: list[AccessReviewItem]
) -> list[Notice]:
    boxes: list[Inbox] = []
    per_project: dict[str, set[str]] = {}
    platform_reviewers: set[str] = set()
    for item in items:
        if item.scope == PROJECT:
            per_project.setdefault(item.subject, set()).update(
                reviewers_of(item)
            )
        else:
            platform_reviewers.update(reviewers_of(item))
    for project, users in sorted(per_project.items()):
        boxes.append(
            Inbox(
                title_key="access_review_pending",
                params={
                    "project": project,
                    "due": campaign.due_at.date().isoformat(),
                },
                link=_project_link(project),
                users=frozenset(users),
                project=project,
            )
        )
    if platform_reviewers:
        boxes.append(
            Inbox(
                title_key="access_review_platform_pending",
                params={"due": campaign.due_at.date().isoformat()},
                link=LINK,
                users=frozenset(platform_reviewers),
            )
        )
    return [
        Notice(
            kind="opened",
            fields=(
                ("Campaign", f"#{campaign.id}"),
                ("Items", str(len(items))),
                ("Projects", str(len(per_project))),
                ("Due", campaign.due_at.date().isoformat()),
                ("By", campaign.started_by),
            ),
            link=LINK,
            inbox=tuple(boxes),
        )
    ]


def open_campaign(db: Session) -> AccessReviewCampaign | None:
    return (
        db.query(AccessReviewCampaign)
        .filter(AccessReviewCampaign.status == OPEN)
        .first()
    )


def start(
    db: Session,
    actor: str,
    now: datetime,
    trigger: str = "manual",
    deadline_days: int | None = None,
) -> tuple[AccessReviewCampaign, list[Notice]]:
    if open_campaign(db) is not None:
        raise ReviewError(409, "A review campaign is already open.")
    days = deadline_days or settings.ACCESS_REVIEW_DEADLINE_DAYS
    if not 1 <= days <= MAX_DEADLINE_DAYS:
        raise ReviewError(
            422, f"The deadline must be 1 to {MAX_DEADLINE_DAYS} days."
        )
    logins, enabled = _last_logins(db, now)
    try:
        infra = directory.group_members(PLATFORM_ADMIN_GROUP)
        items: list[AccessReviewItem] = []
        for project in (
            db.query(Project)
            .filter(Project.status.in_(REVIEWED_STATUSES))
            .order_by(Project.project_name)
        ):
            items.extend(_project_items(project, infra, logins, enabled))
        for group in PLATFORM_GROUPS:
            items.extend(_platform_items(group, infra, logins))
    except (directory.DirectoryError, requests.RequestException) as exc:
        logger.error("Review campaign not started: %s", exc)
        raise ReviewError(
            502, "Keycloak could not list the members, nothing was started."
        ) from exc

    campaign = AccessReviewCampaign(
        status=OPEN,
        trigger=trigger,
        started_by=actor,
        started_at=now,
        due_at=now + timedelta(days=days),
    )
    db.add(campaign)
    db.flush()
    for item in items:
        item.campaign_id = campaign.id
    db.add_all(items)
    db.commit()
    return campaign, _opened_notices(campaign, items)


def maybe_auto_start(db: Session, now: datetime) -> list[Notice]:
    if not settings.ACCESS_REVIEW_AUTO_START:
        return []
    if open_campaign(db) is not None:
        return []
    last = db.query(func.max(AccessReviewCampaign.started_at)).scalar()
    interval = timedelta(days=settings.ACCESS_REVIEW_INTERVAL_DAYS)
    if last is not None and now - last < interval:
        return []
    campaign, notices = start(db, "cmp", now, trigger="scheduled")
    record_system_event(
        "access_review.start",
        f"campaign:{campaign.id}",
        {"campaign_id": campaign.id, "trigger": "scheduled"},
    )
    return notices


# ── Decide ───────────────────────────────────────────────────────────────────


def _get_item(db: Session, item_id: int) -> AccessReviewItem:
    item = db.get(AccessReviewItem, item_id)
    if item is None:
        raise ReviewError(404, "Review item not found.")
    return item


def _check_decision(
    item: AccessReviewItem, decision: str, new_role: str | None
) -> str | None:
    if decision not in DECISIONS:
        raise ReviewError(422, f"Unknown decision '{decision}'.")
    if decision != "change_role":
        return None
    if item.scope == PLATFORM:
        raise ReviewError(
            422, "Platform groups have no roles: keep or remove."
        )
    allowed = [r.value for r in ASSIGNABLE]
    if new_role not in allowed or new_role == item.role:
        raise ReviewError(
            422, f"The new role must be one of {', '.join(allowed)}."
        )
    return new_role


def _authorize(
    db: Session, item: AccessReviewItem, actor: str, actor_is_infra: bool
) -> None:
    campaign = db.get(AccessReviewCampaign, item.campaign_id)
    if campaign is None or campaign.status != OPEN:
        raise ReviewError(409, "This campaign is closed.")
    if actor == item.username:
        raise ReviewError(
            403, "Four eyes: someone else must review your access."
        )
    if item.scope == PLATFORM and not actor_is_infra:
        raise ReviewError(403, "Platform groups are reviewed by infra.")
    if actor not in reviewers_of(item):
        raise ReviewError(
            403,
            "Only the project owner (or its admins when the owner is gone) "
            "reviews this access.",
        )
    if item.scope == PROJECT:
        _authorize_live(db, item, actor, actor_is_infra)


def _authorize_live(
    db: Session, item: AccessReviewItem, actor: str, actor_is_infra: bool
) -> None:
    """
    The reviewer list is frozen at the start, authority is not: someone
    demoted or removed since then no longer decides. Same order as at the
    start: the owner, else a current admin, else current infra.
    """
    project = (
        db.query(Project).filter(Project.project_name == item.subject).first()
    )
    owner = project.owner_username if project else None
    if actor == owner:
        return
    refused = ReviewError(
        403,
        "You no longer hold the role that made you a reviewer of this "
        "access.",
    )
    if owner and owner in reviewers_of(item):
        raise refused
    try:
        # Live group roles only: platform_project_role would make every
        # infra member a project admin here.
        admins = {
            m["username"]
            for m in keycloak_service.list_project_members(item.subject)
            if m["role"] == ProjectRole.ADMIN.value
        }
    except requests.RequestException as exc:
        logger.error("Reviewer %s not verified: %s", actor, exc)
        raise ReviewError(
            503, "Keycloak could not confirm your role, try again."
        ) from exc
    if actor in admins:
        return
    # Infra decides only where no reviewer is a project admin any more,
    # which is the start rule's fallback applied today.
    if actor_is_infra and not admins & set(reviewers_of(item)):
        return
    raise refused


def _apply_project(item: AccessReviewItem, decision: str) -> str:
    """Change the membership. Returns the outcome detail; raises on error."""
    try:
        keycloak_service.remove_user_from_project(item.username, item.subject)
    except ValueError:
        if decision == "remove":
            return "The user no longer exists in Keycloak."
        raise
    if decision == "remove":
        return f"Removed from {item.subject}."
    try:
        keycloak_service.add_user_to_project(
            item.username, item.subject, item.new_role or ""
        )
    except Exception:
        # Put the previous role back rather than leave them with none.
        keycloak_service.add_user_to_project(
            item.username, item.subject, item.role
        )
        raise
    return f"Role changed from {item.role} to {item.new_role}."


def decide(  # pylint: disable=too-many-arguments
    db: Session,
    item_id: int,
    actor: str,
    actor_is_infra: bool,
    decision: str,
    new_role: str | None = None,
    note: str = "",
    now: datetime | None = None,
) -> tuple[AccessReviewItem, list[Notice]]:
    item = _get_item(db, item_id)
    _authorize(db, item, actor, actor_is_infra)
    new_role = _check_decision(item, decision, new_role)
    now = now or utcnow()
    claimed = db.execute(
        update(AccessReviewItem)
        .where(
            AccessReviewItem.id == item.id,
            AccessReviewItem.decision.is_(None),
        )
        .values(
            decision=decision,
            new_role=new_role,
            note=note.strip()[:NOTE_MAX] or None,
            decided_by=actor,
            decided_at=now,
            outcome="applying",
            outcome_detail=None,
        )
    )
    db.commit()
    if claimed.rowcount != 1:
        raise ReviewError(409, "This access was already reviewed.")
    db.refresh(item)

    if decision == "keep":
        item.outcome = "unchanged"
        db.commit()
        return item, []

    if item.scope == PLATFORM:
        return item, _terraform_task(db, item)

    try:
        item.outcome_detail = _apply_project(item, decision)
    except (ValueError, requests.RequestException) as exc:
        logger.error("Review item %s not applied: %s", item.id, exc)
        # Undecided again, so the reviewer can retry; the failure stays in
        # the journal through the audit middleware.
        item.decision = None
        item.new_role = None
        item.decided_by = None
        item.decided_at = None
        item.outcome = "failed"
        item.outcome_detail = str(exc)[:NOTE_MAX]
        db.commit()
        raise ReviewError(
            502, "Keycloak refused the change, nothing was applied."
        ) from exc
    item.outcome = "applied"
    db.commit()
    return item, []


def _terraform_task(db: Session, item: AccessReviewItem) -> list[Notice]:
    hint = terraform_hint(item.username, item.subject)
    item.outcome = "terraform_task"
    item.outcome_detail = hint
    db.commit()
    record_system_event(
        "access_review.terraform_task",
        f"{item.username}:{item.subject}",
        {"item_id": item.id, "campaign_id": item.campaign_id, "hint": hint},
        actor=item.decided_by or "cmp",
    )
    return [
        Notice(
            kind="terraform_task",
            fields=(
                ("User", item.username),
                ("Group", item.subject),
                ("Decided by", item.decided_by or ""),
                ("Change", hint),
            ),
            link=LINK,
            inbox=(
                Inbox(
                    title_key="access_review_terraform_task",
                    params={"user": item.username, "group": item.subject},
                    link=LINK,
                    to_infra=True,
                ),
            ),
        )
    ]


def task_done(
    db: Session, item_id: int, actor: str, now: datetime
) -> AccessReviewItem:
    item = _get_item(db, item_id)
    if item.outcome != "terraform_task" or item.task_done_at is not None:
        raise ReviewError(409, "No open Terraform task on this item.")
    item.task_done_by = actor
    item.task_done_at = now
    db.commit()
    return item


# ── Deadline and close ───────────────────────────────────────────────────────


def escalate(db: Session, now: datetime) -> list[Notice]:
    """Report undecided items once the deadline passed. Changes no access."""
    out: list[Notice] = []
    for campaign in (
        db.query(AccessReviewCampaign)
        .filter(
            AccessReviewCampaign.status == OPEN,
            AccessReviewCampaign.due_at <= now,
            AccessReviewCampaign.escalated_at.is_(None),
        )
        .all()
    ):
        undecided = (
            db.query(AccessReviewItem)
            .filter(
                AccessReviewItem.campaign_id == campaign.id,
                AccessReviewItem.decision.is_(None),
            )
            .all()
        )
        campaign.escalated_at = now
        db.commit()
        if not undecided:
            continue
        subjects = sorted({i.subject for i in undecided})
        late = sorted({r for i in undecided for r in reviewers_of(i)})
        record_system_event(
            "access_review.escalate",
            f"campaign:{campaign.id}",
            {
                "campaign_id": campaign.id,
                "undecided": len(undecided),
                "subjects": subjects,
                "reviewers": late,
            },
        )
        out.append(
            Notice(
                kind="escalated",
                fields=(
                    ("Campaign", f"#{campaign.id}"),
                    ("Undecided", str(len(undecided))),
                    ("Where", ", ".join(subjects)),
                    ("Reviewers", ", ".join(late) or "none"),
                ),
                link=LINK,
                inbox=(
                    Inbox(
                        title_key="access_review_escalated",
                        params={"count": len(undecided)},
                        link=LINK,
                        users=frozenset(late),
                        to_infra=True,
                    ),
                ),
            )
        )
    return out


def close(
    db: Session, campaign_id: int, actor: str, now: datetime
) -> AccessReviewCampaign:
    campaign = db.get(AccessReviewCampaign, campaign_id)
    if campaign is None:
        raise ReviewError(404, "Campaign not found.")
    if campaign.status != OPEN:
        raise ReviewError(409, "This campaign is already closed.")
    campaign.status = CLOSED
    campaign.closed_at = now
    campaign.closed_by = actor
    db.commit()
    return campaign


# ── Reading ──────────────────────────────────────────────────────────────────


def items_of(db: Session, campaign_id: int) -> list[AccessReviewItem]:
    return (
        db.query(AccessReviewItem)
        .filter(AccessReviewItem.campaign_id == campaign_id)
        .order_by(
            AccessReviewItem.scope,
            AccessReviewItem.subject,
            AccessReviewItem.username,
        )
        .all()
    )


def pending_for(
    db: Session, username: str, project: str | None = None
) -> list[AccessReviewItem]:
    """Project items of the open campaign *username* may decide on."""
    campaign = open_campaign(db)
    if campaign is None:
        return []
    query = db.query(AccessReviewItem).filter(
        AccessReviewItem.campaign_id == campaign.id,
        AccessReviewItem.scope == PROJECT,
    )
    if project:
        query = query.filter(AccessReviewItem.subject == project)
    return [
        i
        for i in query.order_by(
            AccessReviewItem.subject, AccessReviewItem.username
        )
        if username in reviewers_of(i)
    ]


EXPORT_FIELDS = (
    "campaign_id",
    "scope",
    "subject",
    "username",
    "role",
    "last_login_at",
    "reviewers",
    "decision",
    "new_role",
    "note",
    "decided_by",
    "decided_at",
    "outcome",
    "outcome_detail",
    "task_done_by",
    "task_done_at",
)


def _stamp(value: datetime | None) -> str | None:
    return f"{value.isoformat()}Z" if value else None


def export_rows(items: list[AccessReviewItem]) -> list[dict]:
    rows = []
    for item in items:
        row = {name: getattr(item, name) for name in EXPORT_FIELDS}
        row["reviewers"] = reviewers_of(item)
        for name in ("last_login_at", "decided_at", "task_done_at"):
            row[name] = _stamp(row[name])
        rows.append(row)
    return rows
