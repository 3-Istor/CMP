"""deployment_resumed

Revision ID: c4f8a2d6e1b9
Revises: e7b3d1a9c5f2
Create Date: 2026-11-17 11:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c4f8a2d6e1b9"
down_revision: Union[str, Sequence[str], None] = "e7b3d1a9c5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column(
            "resumed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("deployments") as batch:
        batch.drop_column("resumed")
