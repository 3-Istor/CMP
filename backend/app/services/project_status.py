import logging

from app.core.database import SessionLocal
from app.models.project import Project, ProjectStatus

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 255


def set_project_status(
    project_name: str, status: ProjectStatus, message: str | None = None
) -> None:
    """Record what a background bootstrap or teardown is doing.

    Runs in its own session because it is called from background tasks, after
    the request's session is gone. A failure to record must never abort the
    Terraform run it is reporting on.
    """
    try:
        with SessionLocal() as db:
            project = (
                db.query(Project)
                .filter(Project.project_name == project_name)
                .first()
            )
            if project is None:
                return
            project.status = status
            project.step_message = (
                message[:_MAX_MESSAGE_LENGTH] if message else None
            )
            db.commit()
    except Exception:  # noqa: BLE001 - status is informative, not critical
        logger.exception(
            "Could not record status '%s' for project '%s'",
            status.value,
            project_name,
        )


def delete_project_row(project_name: str) -> None:
    with SessionLocal() as db:
        db.query(Project).filter(Project.project_name == project_name).delete()
        db.commit()
