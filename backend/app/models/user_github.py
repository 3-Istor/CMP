"""User GitHub Installation model."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserGitHubInstallation(Base):
    """
    Stores GitHub App installation IDs for users.

    This table links Keycloak user IDs (sub) to GitHub App installation IDs.
    When a user links their GitHub account, we store the installation_id here.
    """

    __tablename__ = "user_github_installations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Keycloak user UUID (from JWT 'sub' claim)
    user_sub: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )

    # GitHub App installation ID
    installation_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()  # pylint: disable=not-callable
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )
