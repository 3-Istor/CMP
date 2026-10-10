"""add_alert_settings

Revision ID: d4b8e1f6a2c3
Revises: c9e4a7b2d5f1
Create Date: 2026-10-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b8e1f6a2c3"
down_revision: Union[str, Sequence[str], None] = "c9e4a7b2d5f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Catalogue alerts switched on per project or app."""
    op.create_table(
        "alert_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project", sa.String(100), nullable=False),
        sa.Column("app", sa.String(100), nullable=False),
        sa.Column("alert_id", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("namespaces", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project", "app", "alert_id"),
    )
    op.create_index("ix_alert_settings_project", "alert_settings", ["project"])


def downgrade() -> None:
    op.drop_table("alert_settings")
