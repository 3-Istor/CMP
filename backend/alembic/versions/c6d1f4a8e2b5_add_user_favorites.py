"""add_user_favorites

Revision ID: c6d1f4a8e2b5
Revises: a7d3e9b1c5f2
Create Date: 2026-11-02 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6d1f4a8e2b5"
down_revision: Union[str, Sequence[str], None] = "a7d3e9b1c5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column() -> bool:
    columns = sa.inspect(op.get_bind()).get_columns("user_preferences")
    return any(c["name"] == "favorites" for c in columns)


def upgrade() -> None:
    """Starred projects and apps, synced per user."""
    # The app calls create_all at startup, so the column may already exist.
    if _has_column():
        return
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(sa.Column("favorites", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("favorites")
