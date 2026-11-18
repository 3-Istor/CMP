"""add_issue_reports

Revision ID: b7e2d4f8a1c3
Revises: c4f8a2d6e1b9
Create Date: 2026-11-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2d4f8a1c3"
down_revision: Union[str, Sequence[str], None] = "c4f8a2d6e1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Issue reports sent by assistants (CNP-310)."""
    # The app calls create_all at startup, so the tables may already exist.
    if sa.inspect(op.get_bind()).has_table("issue_reports"):
        return
    op.create_table(
        "issue_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("tool", sa.String(100), nullable=True),
        sa.Column("steps", sa.Text(), nullable=True),
        sa.Column("project", sa.String(100), nullable=True),
        sa.Column("assistant", sa.String(200), nullable=True),
        sa.Column("reporters", sa.JSON(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column(
            "last_reported_at", sa.DateTime(), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_issue_reports_id", "issue_reports", ["id"])
    op.create_table(
        "issue_report_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("issue_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_issue_report_submissions_user_at",
        "issue_report_submissions",
        ["username", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("issue_report_submissions")
    op.drop_table("issue_reports")
