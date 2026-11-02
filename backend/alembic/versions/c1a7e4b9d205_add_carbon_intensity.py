"""add carbon intensity

Revision ID: c1a7e4b9d205
Revises: aeef32a6a294
Create Date: 2026-10-27 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c1a7e4b9d205"
down_revision: Union[str, Sequence[str], None] = "aeef32a6a294"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "carbon_intensity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("zone", sa.String(length=8), nullable=False),
        sa.Column("g_per_kwh", sa.Float(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day", "zone", "method"),
    )
    op.create_index(
        op.f("ix_carbon_intensity_day"), "carbon_intensity", ["day"]
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_carbon_intensity_day"), table_name="carbon_intensity"
    )
    op.drop_table("carbon_intensity")
