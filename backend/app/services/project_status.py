import logging

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
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


# Bootstraps, teardowns and app runs are background tasks of this process: a
# restart kills them, and nothing else moves them out of these states.
_INTERRUPTED_PROJECTS = {
    ProjectStatus.PROVISIONING: (
        ProjectStatus.FAILED,
        "Bootstrap interrupted by a CMP restart. Retry to resume.",
    ),
    ProjectStatus.DECOMMISSIONING: (
        ProjectStatus.DECOMMISSION_FAILED,
        "Teardown interrupted by a CMP restart. Delete again to resume.",
    ),
}
_INTERRUPTED_APPS = {
    **dict.fromkeys(
        (
            DeploymentStatus.PENDING,
            DeploymentStatus.INITIALIZING,
            DeploymentStatus.PLANNING,
            DeploymentStatus.DEPLOYING,
        ),
        "Deployment interrupted by a CMP restart.",
    ),
    DeploymentStatus.DELETING: (
        "Deletion interrupted by a CMP restart. Delete again to resume."
    ),
}


def fail_interrupted_runs() -> None:
    """Mark the runs a restart killed as failed, so they can be retried.

    Call at startup only: the backend is a single replica, so no run is alive.
    """
    with SessionLocal() as db:
        for state, (failed, message) in _INTERRUPTED_PROJECTS.items():
            for project in db.query(Project).filter(Project.status == state):
                logger.warning(
                    "Project '%s' was %s when the CMP stopped",
                    project.project_name,
                    state.value,
                )
                project.status, project.step_message = failed, message
        for state, message in _INTERRUPTED_APPS.items():
            for app in db.query(Deployment).filter(Deployment.status == state):
                logger.warning(
                    "App '%s' was %s when the CMP stopped",
                    app.name,
                    state.value,
                )
                app.status, app.step_message = DeploymentStatus.FAILED, message
        db.commit()
