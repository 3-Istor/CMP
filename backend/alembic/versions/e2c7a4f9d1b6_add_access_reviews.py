"""add_access_reviews

Revision ID: e2c7a4f9d1b6
Revises: d7a3e9c1b5f2
Create Date: 2026-11-11 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e2c7a4f9d1b6"
down_revision: Union[str, Sequence[str], None] = "d7a3e9c1b5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "access_review_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("started_by", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("escalated_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_by", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_access_review_campaigns_status",
        "access_review_campaigns",
        ["status"],
    )
    op.create_table(
        "access_review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("access_review_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("reviewers", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column("new_role", sa.String(32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("outcome_detail", sa.Text(), nullable=True),
        sa.Column("task_done_by", sa.String(255), nullable=True),
        sa.Column("task_done_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_access_review_items_campaign_id",
        "access_review_items",
        ["campaign_id"],
    )
    op.create_index(
        "ix_access_review_items_subject", "access_review_items", ["subject"]
    )
    op.create_index(
        "ix_access_review_items_username",
        "access_review_items",
        ["username"],
    )
    op.create_table(
        "account_activity",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("tracked_since", sa.DateTime(), nullable=False),
        sa.Column("flagged_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_account_activity_username", "account_activity", ["username"]
    )


def downgrade() -> None:
    op.drop_table("account_activity")
    op.drop_table("access_review_items")
    op.drop_table("access_review_campaigns")
