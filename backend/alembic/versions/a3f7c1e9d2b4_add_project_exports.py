"""add_project_exports

Revision ID: a3f7c1e9d2b4
Revises: e2c7a4f9d1b6
Create Date: 2026-11-13 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a3f7c1e9d2b4"
down_revision: Union[str, Sequence[str], None] = "e2c7a4f9d1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_exports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project", sa.String(100), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("include_secret_values", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("object_key", sa.String(255), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("problems", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_project_exports_project", "project_exports", ["project"]
    )
    op.create_index(
        "ix_project_exports_status", "project_exports", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_exports_status", table_name="project_exports")
    op.drop_index("ix_project_exports_project", table_name="project_exports")
    op.drop_table("project_exports")
