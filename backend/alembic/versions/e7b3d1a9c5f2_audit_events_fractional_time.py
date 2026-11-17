"""audit_events_fractional_time

Revision ID: e7b3d1a9c5f2
Revises: d9a4b6e2c8f1
Create Date: 2026-11-17 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e7b3d1a9c5f2"
down_revision: Union[str, Sequence[str], None] = "d9a4b6e2c8f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite compares datetimes as text: rows stored without a fraction
    # (CURRENT_TIMESTAMP) must match the format SQLAlchemy binds.
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "UPDATE audit_events SET created_at = created_at || '.000000' "
            "WHERE created_at NOT LIKE '%.%'"
        )


def downgrade() -> None:
    pass
