"""release_events_environment

Revision ID: f3a9c7e1d5b2
Revises: d5e2a8c4f1b7
Create Date: 2026-11-05 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a9c7e1d5b2"
down_revision: Union[str, Sequence[str], None] = "d5e2a8c4f1b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Environment and component of a release; existing rows are prod."""
    op.add_column(
        "release_events",
        sa.Column(
            "environment",
            sa.String(16),
            nullable=False,
            server_default="prod",
        ),
    )
    op.add_column(
        "release_events",
        sa.Column("component", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("release_events") as batch_op:
        batch_op.drop_column("component")
        batch_op.drop_column("environment")
