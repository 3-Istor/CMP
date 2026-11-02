"""add finops state

Revision ID: d8f3a6c1e4b7
Revises: c1a7e4b9d205
Create Date: 2026-10-27 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d8f3a6c1e4b7"
down_revision: Union[str, Sequence[str], None] = "c1a7e4b9d205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "finops_state",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("finops_state")
