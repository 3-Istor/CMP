"""add_environments

Revision ID: b5e9c2f7a1d8
Revises: f3c1a8e5d7b2
Create Date: 2026-10-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b5e9c2f7a1d8"
down_revision: Union[str, Sequence[str], None] = "f3c1a8e5d7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Staging environments: promotion policies, verification runs, promotions."""
    op.create_table(
        "app_environments",
        sa.Column("deployment_id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(16), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "promotion_policies",
        sa.Column("deployment_id", sa.Integer(), primary_key=True),
        sa.Column("suites", sa.JSON(), nullable=False),
        sa.Column("soak_minutes", sa.Integer(), nullable=False),
        sa.Column("require_approval", sa.Boolean(), nullable=False),
        sa.Column("auto_promote", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "verification_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deployment_id", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("suite", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("digests", sa.JSON(), nullable=False),
        sa.Column("version", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("details_url", sa.String(512), nullable=True),
        sa.Column("measurements", sa.JSON(), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("promotion_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_verification_runs_deployment_id",
        "verification_runs",
        ["deployment_id"],
    )
    op.create_index(
        "ix_verification_runs_promotion_id",
        "verification_runs",
        ["promotion_id"],
    )
    op.create_table(
        "promotions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deployment_id", sa.Integer(), nullable=False),
        sa.Column("source_env", sa.String(16), nullable=False),
        sa.Column("target_env", sa.String(16), nullable=False),
        sa.Column("version", sa.String(128), nullable=True),
        sa.Column("digests", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("emergency", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("rollback_of", sa.Integer(), nullable=True),
        sa.Column("approvals", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_promotions_deployment_id", "promotions", ["deployment_id"]
    )


def downgrade() -> None:
    op.drop_table("promotions")
    op.drop_table("verification_runs")
    op.drop_table("promotion_policies")
    op.drop_table("app_environments")
