"""User GitHub Installation model."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserGitHubInstallation(Base):
    """
    Stores GitHub App installation IDs for users.

    This table links Keycloak users to GitHub App installation IDs.
    When a user links their GitHub account, we store the installation_id here.
    """

    __tablename__ = "user_github_installations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Canonical Keycloak UUID; legacy records may contain preferred_username.
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


class GitHubOAuthCredential(Base):
    """Encrypted OAuth grant for one existing CNP user; never a platform grant."""

    __tablename__ = "github_oauth_credentials"
    user_sub: Mapped[str] = mapped_column(String(255), primary_key=True)
    github_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    github_login: Mapped[str] = mapped_column(String(39), nullable=False)
    credentials_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )
    refresh_expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )
    refreshing_until: Mapped[datetime | None] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GitHubOAuthState(Base):
    """Hashed, single-use state bound to the authenticated browser session."""

    __tablename__ = "github_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_sub: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
