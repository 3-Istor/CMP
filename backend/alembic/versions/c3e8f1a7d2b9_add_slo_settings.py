"""add_slo_settings

Revision ID: c3e8f1a7d2b9
Revises: b7d2e4f1a9c3
Create Date: 2026-11-14 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c3e8f1a7d2b9"
down_revision: Union[str, Sequence[str], None] = "b7d2e4f1a9c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """SLO targets an app owner sets (CNP-185, D14)."""
    op.create_table(
        "slo_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project", sa.String(100), nullable=False),
        sa.Column("app", sa.String(100), nullable=False),
        sa.Column("availability", sa.Float(), nullable=False),
        sa.Column("latency", sa.Float(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project", "app"),
    )
    op.create_index("ix_slo_settings_project", "slo_settings", ["project"])


def downgrade() -> None:
    op.drop_table("slo_settings")
