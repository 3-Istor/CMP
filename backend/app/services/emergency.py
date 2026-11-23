"""Outage view for platform admins: cluster states, exposed projects, moves."""

from sqlalchemy.orm import Session

from app.models.project import TargetCloud
from app.models.project_move import ACTIVE, ProjectMove
from app.models.user_preference import UserPreference
from app.services import project_registry
from app.services.clusters.state import PUBLIC_CLOUDS, cached_state

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
) -> dict:
    """Aggregate; projects of clusters that are not up come first-sorted."""
    projects = []
    for record in records:
        spec = record.get("spec") or {}
        cloud = spec.get("targetCloud")
        state = states.get(cloud, "unknown")
        if state == "up":
            continue
        name = record["metadata"]["name"]
        projects.append(
            {
                "name": name,
                "cloud": cloud,
                "cloud_state": state,
                "apps": [a["name"] for a in spec.get("apps") or []],
                "protected": spec.get("deletionProtection") is True,
                "favorite": name in favorites,
            }
        )
    projects.sort(
        key=lambda p: (
            p["cloud_state"] != "down",
            not p["protected"],
            not p["favorite"],
            p["name"],
        )
    )
    rows = sorted(
        (_move_row(m) for m in moves),
        key=lambda m: (
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


async def overview(db: Session, user_key: str) -> dict:
    record = (
        db.query(UserPreference)
        .filter(UserPreference.user_sub == user_key)
        .first()
    )
    favorites = set(((record and record.favorites) or {}).get("projects", []))
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
    )
