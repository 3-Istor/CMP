"""import_source_mode

Revision ID: f7b3d9a2c4e6
Revises: e5a2c8f4b1d7
Create Date: 2026-11-24 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f7b3d9a2c4e6"
down_revision: Union[str, Sequence[str], None] = "e5a2c8f4b1d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("import_sources") as batch:
        batch.add_column(
            sa.Column(
                "source_mode",
                sa.String(16),
                nullable=False,
                server_default="in_place",
            )
        )
        batch.add_column(sa.Column("origin_repo_full_name", sa.String(200)))
        batch.add_column(sa.Column("origin_repo_id", sa.Integer()))
        batch.add_column(sa.Column("origin_installation_id", sa.Integer()))
        batch.add_column(sa.Column("copy", sa.JSON()))


def downgrade() -> None:
    with op.batch_alter_table("import_sources") as batch:
        batch.drop_column("copy")
        batch.drop_column("origin_installation_id")
        batch.drop_column("origin_repo_id")
        batch.drop_column("origin_repo_full_name")
        batch.drop_column("source_mode")
