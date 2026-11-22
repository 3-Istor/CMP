"""import_recommendation_dismissals

Revision ID: d5a9e3c7b1f4
Revises: c4f8a2d6e1b9
Create Date: 2026-11-18 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d5a9e3c7b1f4"
down_revision: Union[str, Sequence[str], None] = "b7e2d4f8a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_recommendation_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_name", sa.String(100), nullable=False),
        sa.Column("recommendation_id", sa.String(200), nullable=False),
        sa.Column("dismissed_by", sa.String(255), nullable=False),
        sa.Column(
            "dismissed_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_name",
            "recommendation_id",
            name="uq_import_recommendation_dismissals",
        ),
    )
    op.create_index(
        "ix_import_recommendation_dismissals_project_name",
        "import_recommendation_dismissals",
        ["project_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_import_recommendation_dismissals_project_name",
        table_name="import_recommendation_dismissals",
    )
    op.drop_table("import_recommendation_dismissals")
