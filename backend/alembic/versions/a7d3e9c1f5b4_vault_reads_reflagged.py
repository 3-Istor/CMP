"""activity_events: Vault secret reads are reads, a service account's are not notable

Revision ID: a7d3e9c1f5b4
Revises: e5a2c8f4b1d7
Create Date: 2026-11-24 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "a7d3e9c1f5b4"
down_revision: Union[str, Sequence[str], None] = "e5a2c8f4b1d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE activity_events SET is_read = 1 "
        "WHERE source = 'vault' AND action IN ('vault.read', 'vault.list')"
    )
    op.execute(
        "UPDATE activity_events SET notable = 0 "
        "WHERE source = 'vault' AND action IN ('vault.read', 'vault.list') "
        "AND actor LIKE 'kubernetes-%'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE activity_events SET is_read = 0 "
        "WHERE source = 'vault' AND action = 'vault.read'"
    )
