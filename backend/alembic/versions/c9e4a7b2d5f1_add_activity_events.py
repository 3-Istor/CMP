"""add_activity_events

Revision ID: c9e4a7b2d5f1
Revises: b3f8d2c6e1a9
Create Date: 2026-10-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e4a7b2d5f1"
down_revision: Union[str, Sequence[str], None] = "b3f8d2c6e1a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Audit events collected from Loki and Argo CD, and the read cursors."""
    op.create_table(
        "activity_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(80), nullable=False, unique=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project", sa.String(100), nullable=True),
        sa.Column("app", sa.String(100), nullable=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("notable", sa.Boolean(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("target", sa.String(512), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("source_ip", sa.String(64), nullable=True),
        sa.Column("details", sa.Text(), nullable=False),
    )
    for column in ("source", "time", "project", "actor", "action"):
        op.create_index(
            f"ix_activity_events_{column}", "activity_events", [column]
        )
    op.create_table(
        "activity_cursors",
        sa.Column("source", sa.String(16), primary_key=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("activity_cursors")
    op.drop_table("activity_events")
