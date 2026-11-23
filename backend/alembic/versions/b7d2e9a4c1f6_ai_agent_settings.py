"""ai_agents settings read from the values file, credentials state

Revision ID: b7d2e9a4c1f6
Revises: c4e8a2d6f1b3
Create Date: 2026-11-23 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7d2e9a4c1f6"
down_revision: Union[str, Sequence[str], None] = "c4e8a2d6f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_agents",
        sa.Column("settings_status", sa.String(16), nullable=True),
    )
    op.add_column(
        "ai_agents",
        sa.Column(
            "settings_read_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "ai_agents", sa.Column("credentials", sa.String(8), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_agents", "credentials")
    op.drop_column("ai_agents", "settings_read_at")
    op.drop_column("ai_agents", "settings_status")
