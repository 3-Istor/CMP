"""savings ledger: one row per cloud

Revision ID: e4a7c2d9f1b3
Revises: b8d3f1e6c4a2
Create Date: 2026-10-27 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e4a7c2d9f1b3"
down_revision: Union[str, Sequence[str], None] = "b8d3f1e6c4a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KEY = ("day", "project", "app", "category")
NEW_KEY = (*OLD_KEY, "cloud")


def _table(key: tuple[str, ...], name: str) -> sa.Table:
    # The unique constraint was created unnamed, which SQLite cannot alter in
    # place: batch mode rebuilds the table from this description.
    return sa.Table(
        "savings_ledger",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("project", sa.String(length=100), nullable=False),
        sa.Column("app", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("avoided_eur", sa.Float(), nullable=False),
        sa.Column("realized_eur", sa.Float(), nullable=False),
        sa.Column("energy_avoided_kwh", sa.Float(), nullable=False),
        sa.Column("co2e_avoided_kg", sa.Float(), nullable=False),
        sa.Column("cloud", sa.String(length=16), nullable=False),
        sa.UniqueConstraint(*key, name=name),
        sa.Index("ix_savings_ledger_day", "day"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "savings_ledger",
        copy_from=_table(OLD_KEY, "uq_savings_ledger_day"),
        recreate="always",
    ) as batch:
        batch.drop_constraint("uq_savings_ledger_day", type_="unique")
        batch.create_unique_constraint("uq_savings_ledger_day_cloud", NEW_KEY)


def downgrade() -> None:
    with op.batch_alter_table(
        "savings_ledger",
        copy_from=_table(NEW_KEY, "uq_savings_ledger_day_cloud"),
        recreate="always",
    ) as batch:
        batch.drop_constraint("uq_savings_ledger_day_cloud", type_="unique")
        batch.create_unique_constraint("uq_savings_ledger_day", OLD_KEY)
