"""Persistent repository import permissions and state.

Revision ID: e87bd1f923ab
Revises: a7c31f5b92d4
"""

from alembic import op
import sqlalchemy as sa

revision = "e87bd1f923ab"
down_revision = "a7c31f5b92d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repository_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_name", sa.String(100), nullable=False, index=True),
        sa.Column("app_name", sa.String(63), nullable=False, unique=True),
        sa.Column(
            "repository_url", sa.String(255), nullable=False, unique=True
        ),
        sa.Column("user_sub", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("installation_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(100)),
        sa.Column("source_sha", sa.String(40)),
        sa.Column("pull_request", sa.Integer()),
        sa.Column(
            "deployment_id",
            sa.Integer(),
            sa.ForeignKey("deployments.id"),
            unique=True,
        ),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("message", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("repository_imports")
