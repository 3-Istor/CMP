"""add_user_preferences

Revision ID: a1c5e8f2b7d3
Revises: e4a7c2d9f1b3
Create Date: 2026-11-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c5e8f2b7d3"
down_revision: Union[str, Sequence[str], None] = "e4a7c2d9f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Per-user preferences, starting with the UI language."""
    # The app calls create_all at startup, so the table may already exist.
    if sa.inspect(op.get_bind()).has_table("user_preferences"):
        return
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_sub", sa.String(255), nullable=False),
        sa.Column("locale", sa.String(5), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_user_preferences_id", "user_preferences", ["id"])
    op.create_index(
        "ix_user_preferences_user_sub",
        "user_preferences",
        ["user_sub"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
