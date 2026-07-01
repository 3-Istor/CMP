"""Project ownership model."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProjectOwner(Base):
    """
    Persists the owner (creator) of a project.

    The rest of the project RBAC lives in Keycloak groups
    (``project-<name>-admins`` / ``project-<name>-members``). The owner is a
    single immutable user — the person who created the project — who can never
    be removed from the project, on top of always being a project admin.
    """

    __tablename__ = "project_owners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Project identifier (matches the Keycloak group naming, e.g. "sandbox")
    project_name: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )

    # Keycloak username of the owner
    owner_username: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )
