"""security_finding_i18n

Revision ID: b8d4f2a6c1e9
Revises: f3a9c7e1d5b2
Create Date: 2026-11-06 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d4f2a6c1e9"
down_revision: Union[str, Sequence[str], None] = "f3a9c7e1d5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rows written before this get their keys at the next scan of their source.
    with op.batch_alter_table("security_findings") as batch:
        batch.add_column(
            sa.Column("i18n", sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("security_findings") as batch:
        batch.drop_column("i18n")
