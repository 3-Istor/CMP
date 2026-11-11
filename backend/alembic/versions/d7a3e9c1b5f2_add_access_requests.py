"""add_access_requests

Revision ID: d7a3e9c1b5f2
Revises: c4e8a2f6b9d1
Create Date: 2026-11-11 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d7a3e9c1b5f2"
down_revision: Union[str, Sequence[str], None] = "c4e8a2f6b9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "access_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("elevation", sa.String(64), nullable=False),
        sa.Column("requester", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("ended_by", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_access_requests_elevation", "access_requests", ["elevation"]
    )
    op.create_index(
        "ix_access_requests_requester", "access_requests", ["requester"]
    )
    op.create_index("ix_access_requests_status", "access_requests", ["status"])
    op.create_index(
        "ix_access_requests_status_expires",
        "access_requests",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_table("access_requests")
