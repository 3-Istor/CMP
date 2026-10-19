"""add_agent_tables

Revision ID: f3c1a8e5d7b2
Revises: e7a2c5d9b1f3
Create Date: 2026-10-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f3c1a8e5d7b2"
down_revision: Union[str, Sequence[str], None] = "e7a2c5d9b1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """AI assistants connected through the MCP server."""
    op.create_table(
        "agent_clients",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("client_name", sa.String(255), nullable=False),
        sa.Column("info", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "agent_auth_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_auth_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column(
            "redirect_uri_provided_explicitly", sa.Integer(), nullable=False
        ),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("client_name", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index("ix_agent_grants_client_id", "agent_grants", ["client_id"])
    op.create_index("ix_agent_grants_subject", "agent_grants", ["subject"])
    op.create_index("ix_agent_grants_username", "agent_grants", ["username"])
    op.create_table(
        "agent_access_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "grant_id",
            sa.Integer(),
            sa.ForeignKey("agent_grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_agent_access_tokens_grant_id", "agent_access_tokens", ["grant_id"]
    )
    op.create_table(
        "agent_confirmations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("project", sa.String(100), nullable=False),
        sa.Column("deployment_id", sa.Integer(), nullable=True),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_confirmations_grant_id", "agent_confirmations", ["grant_id"]
    )
    op.create_index(
        "ix_agent_confirmations_username", "agent_confirmations", ["username"]
    )
    op.create_index(
        "ix_agent_confirmations_project", "agent_confirmations", ["project"]
    )
    op.create_table(
        "project_agent_settings",
        sa.Column("project_name", sa.String(100), primary_key=True),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column(
            "autonomous_until", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    with op.batch_alter_table("deployments") as batch:
        batch.add_column(sa.Column("created_via", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("deployments") as batch:
        batch.drop_column("created_via")
    op.drop_table("project_agent_settings")
    op.drop_table("agent_confirmations")
    op.drop_table("agent_access_tokens")
    op.drop_table("agent_grants")
    op.drop_table("agent_auth_codes")
    op.drop_table("agent_auth_requests")
    op.drop_table("agent_clients")
