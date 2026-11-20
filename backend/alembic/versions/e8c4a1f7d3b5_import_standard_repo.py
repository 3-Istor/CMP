"""import_standard_repo

Revision ID: e8c4a1f7d3b5
Revises: d5a9e3c7b1f4
Create Date: 2026-11-20 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e8c4a1f7d3b5"
down_revision: Union[str, Sequence[str], None] = "d5a9e3c7b1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("import_sources") as batch:
        batch.add_column(sa.Column("standard_repo", sa.JSON()))


def downgrade() -> None:
    with op.batch_alter_table("import_sources") as batch:
        batch.drop_column("standard_repo")
