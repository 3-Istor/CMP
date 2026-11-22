"""ai_agents

Revision ID: c4e8a2d6f1b3
Revises: a9d3f7b2c6e4
Create Date: 2026-11-22 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c4e8a2d6f1b3"
down_revision: Union[str, Sequence[str], None] = "a9d3f7b2c6e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column("template", sa.String(64), nullable=True),
        sa.Column("role", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("repo", sa.String(255), nullable=True),
        sa.Column("values_path", sa.String(255), nullable=True),
        sa.Column("rights", sa.JSON(), nullable=False),
        sa.Column("declaration", sa.JSON(), nullable=False),
        sa.Column("pull_request", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project", "name"),
    )
    op.create_index("ix_ai_agents_project", "ai_agents", ["project"])


def downgrade() -> None:
    op.drop_index("ix_ai_agents_project", table_name="ai_agents")
    op.drop_table("ai_agents")
