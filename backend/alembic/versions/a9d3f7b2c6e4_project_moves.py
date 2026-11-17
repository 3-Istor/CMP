"""project_moves

Revision ID: a9d3f7b2c6e4
Revises: c4f8a2d6e1b9
Create Date: 2026-11-18 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a9d3f7b2c6e4"
down_revision: Union[str, Sequence[str], None] = "c4f8a2d6e1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_moves",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("target", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("restores", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_project_moves_project", "project_moves", ["project"])


def downgrade() -> None:
    op.drop_index("ix_project_moves_project", table_name="project_moves")
    op.drop_table("project_moves")
