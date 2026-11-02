"""add_app_icons

Revision ID: a7d3e9b1c5f2
Revises: a1c5e8f2b7d3
Create Date: 2026-11-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d3e9b1c5f2"
down_revision: Union[str, Sequence[str], None] = "a1c5e8f2b7d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "deployments", sa.Column("icon_kind", sa.String(16), nullable=True)
    )
    op.add_column(
        "deployments", sa.Column("icon_name", sa.String(32), nullable=True)
    )
    op.add_column(
        "deployments", sa.Column("icon_color", sa.String(16), nullable=True)
    )
    op.add_column(
        "deployments", sa.Column("icon_key", sa.String(255), nullable=True)
    )
    op.add_column(
        "deployments", sa.Column("icon_hash", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("deployments") as batch:
        batch.drop_column("icon_hash")
        batch.drop_column("icon_key")
        batch.drop_column("icon_color")
        batch.drop_column("icon_name")
        batch.drop_column("icon_kind")
