"""Project model."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TargetCloud(str, enum.Enum):
    """The cloud a project runs on.

    Values match the Argo CD cluster secret names, so a record's ``target_cloud``
    can be used directly as an Application ``destination.name``.
    """

    ONPREM = "onprem"
    AWS = "aws"
    GCP = "gcp"


class ProjectStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DECOMMISSIONING = "decommissioning"


class Project(Base):
    """
    A project, and where it runs.

    The owner is a single immutable user — the person who created the project —
    who can never be removed from the project, on top of always being a project
    admin. The rest of the project RBAC lives in Keycloak groups
    (``project-<name>-admins`` / ``project-<name>-members``).

    ``target_cloud`` is a *mirror*: the source of truth for placement is the
    project's record in ``cnp-projects/registry/projects/<name>.yaml`` (D-01).
    This column exists so the portal can list and render projects without
    reading Git on every request. On divergence, the Git record wins — the
    reconciler in WS-9 checks both directions.

    ``target_cloud`` is immutable after creation in v1. Changing it does not
    move a project, it orphans everything already provisioned on the old cloud.
    The registry writer refuses to rewrite it; any future update endpoint must
    exclude it too.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Project identifier (matches the Keycloak group naming, e.g. "sandbox")
    project_name: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )

    # Keycloak username of the owner
    owner_username: Mapped[str] = mapped_column(String(255), nullable=False)

    # values_callable stores the enum *value* ("onprem") rather than SQLAlchemy's
    # default of the member name ("ONPREM"). The stored string is read back out
    # into the Git registry and used as an Argo CD destination name, so it has to
    # be the value.
    target_cloud: Mapped[TargetCloud] = mapped_column(
        Enum(
            TargetCloud,
            name="targetcloud",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=TargetCloud.ONPREM,
        server_default=TargetCloud.ONPREM.value,
    )

    status: Mapped[ProjectStatus] = mapped_column(
        Enum(
            ProjectStatus,
            name="projectstatus",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ProjectStatus.ACTIVE,
        server_default=ProjectStatus.ACTIVE.value,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )
