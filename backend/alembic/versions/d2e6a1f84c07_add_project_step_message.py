"""add projects.step_message

Revision ID: d2e6a1f84c07
Revises: a7c31f5b92d4
Create Date: 2026-10-06 16:00:00.000000

``status`` gains the ``provisioning`` and ``failed`` values. Those need no DDL:
SQLAlchemy's Enum is a plain VARCHAR on SQLite, with no CHECK constraint.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d2e6a1f84c07"
down_revision: Union[str, Sequence[str], None] = "a7c31f5b92d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("projects")}
    if "step_message" in columns:
        return
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column("step_message", sa.String(length=255), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("step_message")
