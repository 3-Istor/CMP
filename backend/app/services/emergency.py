"""Outage view for platform admins: cluster states, exposed projects, moves."""

from sqlalchemy.orm import Session

from app.models.deployment import Deployment
from app.models.project import TargetCloud
from app.models.project_move import ACTIVE, ProjectMove
from app.models.user_preference import UserPreference
from app.services import project_registry
from app.services.authz import PLATFORM_ADMIN_GROUP
from app.services.clusters.state import PUBLIC_CLOUDS, cached_state
from app.services.keycloak_service import list_group_usernames
from app.services.moves import footprint

CLOUDS = (TargetCloud.ONPREM, *PUBLIC_CLOUDS)
FINISHED_SHOWN = 10


def cluster_state(cloud: TargetCloud) -> str:
    # The CMP runs on-prem: if it answers, on-prem is up.
    return "up" if cloud == TargetCloud.ONPREM else cached_state(cloud)


def _move_row(move: ProjectMove) -> dict:
    steps = move.steps or []
    current = next(
        (s["name"] for s in steps if s.get("status") != "done"),
        steps[-1]["name"] if steps else None,
    )
    return {
        "id": move.id,
        "project": move.project,
        "source": move.source,
        "target": move.target,
        "step": current,
        "status": move.status,
        "started_at": move.created_at.isoformat(),
        "auto": move.reason != "button",
    }


def build(
    states: dict[str, str],
    records: list[dict],
    moves: list[ProjectMove],
    favorites: set[str],
    favorite_apps: frozenset[tuple[str, str]] = frozenset(),
    footprints: list[dict] | None = None,
) -> dict:
    """Aggregate: favorites first, then projects on a down cluster."""
    projects = []
    for record in records:
        spec = record.get("spec") or {}
        cloud = spec.get("targetCloud")
        state = states.get(cloud, "unknown")
        if state == "up":
            continue
        name = record["metadata"]["name"]
        apps = [a["name"] for a in spec.get("apps") or []]
        starred = [a for a in apps if (name, a) in favorite_apps]
        projects.append(
            {
                "name": name,
                "cloud": cloud,
                "cloud_state": state,
                "apps": apps,
                # Last requests measured on the project's cluster, per app.
                "footprint": [
                    f
                    for f in footprints or []
                    if f["project"] == name and f["cloud"] == cloud
                ],
                "favorite_apps": starred,
                "protected": spec.get("deletionProtection") is True,
                "favorite": name in favorites or bool(starred),
            }
        )
    projects.sort(
        key=lambda p: (
            not p["favorite"],
            p["cloud_state"] != "down",
            p["name"],
        )
    )
    rows = sorted(
        (_move_row(m) for m in moves),
        key=lambda m: (
            m["status"] != "failed",
            m["status"] not in ACTIVE,
            not m["auto"],
            m["started_at"],
        ),
    )
    return {
        "clusters": [{"cloud": c, "state": s} for c, s in states.items()],
        "projects": projects,
        "moves": rows,
    }


def admin_favorites(
    db: Session, user_key: str
) -> tuple[set[str], frozenset[tuple[str, str]]]:
    """Starred projects and (project, app) pairs of every platform admin; the
    caller's alone if Keycloak cannot list the group."""
    try:
        admins = list_group_usernames(PLATFORM_ADMIN_GROUP)
    except Exception:  # pylint: disable=broad-exception-caught
        admins = set()
    admins.add(user_key)
    records = (
        db.query(UserPreference)
        .filter(UserPreference.user_sub.in_(admins))
        .all()
    )
    projects = {
        n for r in records for n in (r.favorites or {}).get("projects", [])
    }
    ids = {i for r in records for i in (r.favorites or {}).get("apps", [])}
    rows = (
        db.query(Deployment).filter(Deployment.id.in_(ids)).all()
        if ids
        else []
    )
    return projects, frozenset((d.project_id, d.name) for d in rows)


async def overview(db: Session, user_key: str) -> dict:
    favorites, favorite_apps = admin_favorites(db, user_key)
    newest = ProjectMove.created_at.desc()
    active = db.query(ProjectMove).filter(ProjectMove.status.in_(ACTIVE))
    finished = (
        db.query(ProjectMove)
        .filter(ProjectMove.status.notin_(ACTIVE))
        .order_by(newest)
        .limit(FINISHED_SHOWN)
    )
    records = await project_registry.list_records_cached()
    return build(
        {c.value: cluster_state(c) for c in CLOUDS},
        records,
        [*active.all(), *finished.all()],
        favorites,
        favorite_apps,
        footprint.rows(db),
    )
