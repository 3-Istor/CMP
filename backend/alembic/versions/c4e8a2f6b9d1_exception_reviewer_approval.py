"""exception_reviewer_approval

Revision ID: c4e8a2f6b9d1
Revises: b8d4f2a6c1e9
Create Date: 2026-11-07 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c4e8a2f6b9d1"
down_revision: Union[str, Sequence[str], None] = "b8d4f2a6c1e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing approvals were given by project admins: they stop covering a
    # critical finding, which now needs the auditor or a platform admin.
    with op.batch_alter_table("security_exceptions") as batch:
        batch.add_column(
            sa.Column(
                "approved_by_reviewer",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("security_exceptions") as batch:
        batch.drop_column("approved_by_reviewer")
