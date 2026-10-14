"""add_release_events

Revision ID: e7a2c5d9b1f3
Revises: d4b8e1f6a2c3
Create Date: 2026-10-14 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a2c5d9b1f3"
down_revision: Union[str, Sequence[str], None] = "d4b8e1f6a2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """History of progressive releases, one row per attempt."""
    op.create_table(
        "release_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deployment_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("image", sa.String(512), nullable=True),
        sa.Column("strategy", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("reached_step", sa.Integer(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("actors", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_release_events_deployment_id", "release_events", ["deployment_id"]
    )


def downgrade() -> None:
    op.drop_table("release_events")
