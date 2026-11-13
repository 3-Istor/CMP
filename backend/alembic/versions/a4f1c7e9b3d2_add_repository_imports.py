"""add_repository_imports

Revision ID: a4f1c7e9b3d2
Revises: e2c7a4f9d1b6
Create Date: 2026-11-13 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a4f1c7e9b3d2"
down_revision: Union[str, Sequence[str], None] = "e2c7a4f9d1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_name", sa.String(100), nullable=False),
        sa.Column("repo_full_name", sa.String(200), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project_name", "repo_id"),
    )
    op.create_index(
        "ix_import_sources_project_name", "import_sources", ["project_name"]
    )
    op.create_table(
        "import_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("import_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("workflow_run_id", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_import_runs_source_id", "import_runs", ["source_id"])
    op.create_index("ix_import_runs_status", "import_runs", ["status"])
    op.create_table(
        "import_services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("import_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("app_name", sa.String(63), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("last_sha", sa.String(40), nullable=True),
        sa.Column("last_digest", sa.String(71), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "deployment_id",
            sa.Integer(),
            sa.ForeignKey("deployments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_import_services_source_id", "import_services", ["source_id"]
    )
    op.create_table(
        "github_oauth_credentials",
        sa.Column("user_sub", sa.String(255), primary_key=True),
        sa.Column("github_user_id", sa.String(32), nullable=False),
        sa.Column("github_login", sa.String(39), nullable=False),
        sa.Column("credentials_ciphertext", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(), nullable=False),
        sa.Column("refreshing_until", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "github_oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("user_sub", sa.String(255), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_github_oauth_states_user_sub", "github_oauth_states", ["user_sub"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_oauth_states_user_sub", table_name="github_oauth_states"
    )
    op.drop_table("github_oauth_states")
    op.drop_table("github_oauth_credentials")
    op.drop_index("ix_import_services_source_id", table_name="import_services")
    op.drop_table("import_services")
    op.drop_index("ix_import_runs_status", table_name="import_runs")
    op.drop_index("ix_import_runs_source_id", table_name="import_runs")
    op.drop_table("import_runs")
    op.drop_index(
        "ix_import_sources_project_name", table_name="import_sources"
    )
    op.drop_table("import_sources")
