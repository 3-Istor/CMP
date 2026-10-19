"""AI assistants acting on a user's behalf through the MCP server."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentClient(Base):
    """An OAuth client registered by an assistant (dynamic registration)."""

    __tablename__ = "agent_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_name: Mapped[str] = mapped_column(String(255), default="")
    info: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentAuthRequest(Base):
    """An authorization waiting for the user to approve it in the CMP."""

    __tablename__ = "agent_auth_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentAuthCode(Base):
    __tablename__ = "agent_auth_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64))
    claims: Mapped[dict] = mapped_column(JSON)
    scopes: Mapped[list] = mapped_column(JSON)
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(Text)
    redirect_uri_provided_explicitly: Mapped[int] = mapped_column(Integer)
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentGrant(Base):
    """
    One assistant connected to one user's account. Revoking it kills its
    refresh token and every access token issued under it at once.

    ``claims`` is the user's identity as Keycloak stated it at approval: sub,
    username, name, email. Project roles are not stored: they are resolved
    live from Keycloak on every call, like for any other token.
    """

    __tablename__ = "agent_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    client_name: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    claims: Mapped[dict] = mapped_column(JSON)
    scopes: Mapped[list] = mapped_column(JSON)
    refresh_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentAccessToken(Base):
    __tablename__ = "agent_access_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[int] = mapped_column(
        ForeignKey("agent_grants.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentConfirmation(Base):
    """
    A sensitive action an assistant asked for on a critical project. It runs
    only after a human approves it in the CMP, which the model cannot do.
    """

    __tablename__ = "agent_confirmations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    grant_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    project: Mapped[str] = mapped_column(String(100), index=True)
    deployment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectAgentSettings(Base):
    """
    What is at stake in a project, which sets how freely assistants act in
    it. A project without a row is ``standard``.
    """

    __tablename__ = "project_agent_settings"

    project_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="standard")
    autonomous_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
