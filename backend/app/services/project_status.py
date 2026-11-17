import logging

from app.core.database import SessionLocal
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project, ProjectStatus
from app.services.observability import reconcile as obs_reconcile

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
        if status in (ProjectStatus.ACTIVE, ProjectStatus.DECOMMISSIONING):
            obs_reconcile.request_sync()
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
    obs_reconcile.request_sync()


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
RESUME_MESSAGE = "Resuming after a CMP restart."
_INTERRUPTED_RUNS = (
    DeploymentStatus.PENDING,
    DeploymentStatus.INITIALIZING,
    DeploymentStatus.PLANNING,
    DeploymentStatus.DEPLOYING,
)
_INTERRUPTED_DELETE_MESSAGE = (
    "Deletion interrupted by a CMP restart. Delete again to resume."
)


def fail_interrupted_runs() -> list[int]:
    """Fail interrupted projects, imports and deletions; return catalog runs.

    Returned ids are reset to PENDING for the caller to run again. Call at
    startup only: the backend is a single replica, so no run is alive.
    """
    resumed: list[int] = []
    with SessionLocal() as db:
        for state, (failed, message) in _INTERRUPTED_PROJECTS.items():
            for project in db.query(Project).filter(Project.status == state):
                logger.warning(
                    "Project '%s' was %s when the CMP stopped",
                    project.project_name,
                    state.value,
                )
                project.status, project.step_message = failed, message
        interrupted = (
            db.query(Deployment)
            .filter(
                Deployment.status.in_(
                    (*_INTERRUPTED_RUNS, DeploymentStatus.DELETING)
                )
            )
            .order_by(Deployment.id)
        )
        for app in interrupted:
            logger.warning(
                "App '%s' was %s when the CMP stopped",
                app.name,
                app.status.value,
            )
            if app.status == DeploymentStatus.DELETING:
                app.status = DeploymentStatus.FAILED
                app.step_message = _INTERRUPTED_DELETE_MESSAGE
            elif app.template_id.startswith("import-"):
                # The import run resumes these itself.
                app.status = DeploymentStatus.FAILED
                app.step_message = "Deployment interrupted by a CMP restart."
            else:
                app.status = DeploymentStatus.PENDING
                app.step_message = RESUME_MESSAGE
                resumed.append(app.id)
        db.commit()
    return resumed
