"""project_footprints: last pod requests per project, app and cloud

Revision ID: d3b8f1a6c2e9
Revises: b7d2e9a4c1f6
Create Date: 2026-11-23 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d3b8f1a6c2e9"
down_revision: Union[str, Sequence[str], None] = "b7d2e9a4c1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_footprints",
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("app", sa.String(255), nullable=False),
        sa.Column("cloud", sa.String(16), nullable=False),
        sa.Column("cpu", sa.Float(), nullable=False),
        sa.Column("memory_bytes", sa.Float(), nullable=False),
        sa.Column("pods", sa.Integer(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project", "app", "cloud"),
    )


def downgrade() -> None:
    op.drop_table("project_footprints")
