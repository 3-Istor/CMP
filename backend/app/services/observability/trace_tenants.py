"""
One VictoriaTraces tenant per project (D10, decision Q9): AccountID is the
project's number here, ProjectID is always 0. The traces APIs ignore
extra_filters, so the tenant is the only thing that separates two projects'
traces. A number is never reused: a deleted project's row is retired, and a
project created again under the same name gets a new one.
"""

from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.observability import TraceTenant
from app.models.project import Project
from app.services.activity_sources import PROJECT_NAME

PROJECT_ID = 0


def accounts() -> dict[str, int]:
    """Every project's AccountID, allocating and retiring as needed."""
    with SessionLocal() as db:
        projects = {
            p
            for (p,) in db.query(Project.project_name)
            if PROJECT_NAME.match(p)
        }
        active = {
            t.project: t
            for t in db.query(TraceTenant).filter(
                TraceTenant.retired_at.is_(None)
            )
        }
        now = datetime.now(timezone.utc)
        for project, tenant in active.items():
            if project not in projects:
                tenant.retired_at = now
        db.add_all(
            TraceTenant(project=p) for p in sorted(projects - set(active))
        )
        db.commit()
        return {
            t.project: t.id
            for t in db.query(TraceTenant).filter(
                TraceTenant.retired_at.is_(None)
            )
        }
