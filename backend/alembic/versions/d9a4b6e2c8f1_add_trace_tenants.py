"""add_trace_tenants

Revision ID: d9a4b6e2c8f1
Revises: c3e8f1a7d2b9
Create Date: 2026-11-14 01:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d9a4b6e2c8f1"
down_revision: Union[str, Sequence[str], None] = "c3e8f1a7d2b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """One VictoriaTraces tenant per project (CNP-185, Q9)."""
    op.create_table(
        "trace_tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ux_trace_tenants_active_project",
        "trace_tenants",
        ["project"],
        unique=True,
        sqlite_where=sa.text("retired_at IS NULL"),
        postgresql_where=sa.text("retired_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("trace_tenants")
