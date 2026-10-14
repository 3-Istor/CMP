"""add_audit_events

Revision ID: b3f8d2c6e1a9
Revises: a7c3e9d1f2b4
Create Date: 2026-10-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f8d2c6e1a9"
down_revision: Union[str, Sequence[str], None] = "a7c3e9d1f2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Journal of the changes requested through the CMP."""
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("project", sa.String(100), nullable=True),
        sa.Column("app", sa.String(100), nullable=True),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("source_ip", sa.String(64), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_audit_events_created_at", "audit_events", ["created_at"]
    )
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_project", "audit_events", ["project"])


def downgrade() -> None:
    op.drop_table("audit_events")
