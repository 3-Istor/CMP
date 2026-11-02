"""add_notifications

Revision ID: d5e2a8c4f1b7
Revises: c6d1f4a8e2b5
Create Date: 2026-11-02 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e2a8c4f1b7"
down_revision: Union[str, Sequence[str], None] = "c6d1f4a8e2b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Per-user notification centre."""
    # The app calls create_all at startup, so the table may already exist.
    if sa.inspect(op.get_bind()).has_table("notifications"):
        return
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_key", sa.String(255), nullable=False),
        sa.Column("project", sa.String(100), nullable=True),
        sa.Column("app", sa.String(100), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title_key", sa.String(100), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_notifications_id", "notifications", ["id"])
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("notifications")
