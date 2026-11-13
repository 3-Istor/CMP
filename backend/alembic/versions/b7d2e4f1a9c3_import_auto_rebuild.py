"""import_auto_rebuild

Revision ID: b7d2e4f1a9c3
Revises: a4f1c7e9b3d2
Create Date: 2026-11-13 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7d2e4f1a9c3"
down_revision: Union[str, Sequence[str], None] = "a4f1c7e9b3d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("import_sources") as batch:
        batch.add_column(sa.Column("rebuild_user", sa.String(255)))
        batch.add_column(sa.Column("rebuild_user_sub", sa.String(64)))
        batch.add_column(
            sa.Column(
                "auto_rebuild",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
        batch.add_column(sa.Column("head_checked", sa.String(40)))
        batch.add_column(sa.Column("rebuild_pending", sa.JSON()))
    op.create_table(
        "import_webhook_deliveries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_import_webhook_deliveries_received_at",
        "import_webhook_deliveries",
        ["received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_import_webhook_deliveries_received_at",
        table_name="import_webhook_deliveries",
    )
    op.drop_table("import_webhook_deliveries")
    with op.batch_alter_table("import_sources") as batch:
        batch.drop_column("rebuild_pending")
        batch.drop_column("head_checked")
        batch.drop_column("auto_rebuild")
        batch.drop_column("rebuild_user_sub")
        batch.drop_column("rebuild_user")
