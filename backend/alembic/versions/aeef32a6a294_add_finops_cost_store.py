"""add finops cost store

Revision ID: aeef32a6a294
Revises: b5e9c2f7a1d8
Create Date: 2026-10-26 15:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "aeef32a6a294"
down_revision: Union[str, Sequence[str], None] = "b5e9c2f7a1d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cost_line_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("x_source", sa.String(length=32), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("service_name", sa.String(length=128), nullable=False),
        sa.Column("service_category", sa.String(length=64), nullable=False),
        sa.Column("charge_category", sa.String(length=16), nullable=False),
        sa.Column("charge_period_start", sa.DateTime(), nullable=False),
        sa.Column("charge_period_end", sa.DateTime(), nullable=False),
        sa.Column("billing_period", sa.String(length=7), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("resource_name", sa.String(length=255), nullable=False),
        sa.Column("billed_cost", sa.Float(), nullable=False),
        sa.Column("effective_cost", sa.Float(), nullable=False),
        sa.Column("list_cost", sa.Float(), nullable=False),
        sa.Column("billing_currency", sa.String(length=3), nullable=False),
        sa.Column("consumed_quantity", sa.Float(), nullable=True),
        sa.Column("consumed_unit", sa.String(length=32), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("project", sa.String(length=100), nullable=True),
        sa.Column("app", sa.String(length=100), nullable=True),
        sa.Column("env", sa.String(length=32), nullable=True),
        sa.Column("cloud", sa.String(length=16), nullable=False),
        sa.Column("x_cost_eur", sa.Float(), nullable=False),
        sa.Column("x_fx_rate", sa.Float(), nullable=False),
        sa.Column("x_allocation_method", sa.String(length=16), nullable=False),
        sa.Column("x_pricing_basis", sa.String(length=16), nullable=False),
        sa.Column("x_energy_kwh", sa.Float(), nullable=True),
        sa.Column("x_co2e_kg", sa.Float(), nullable=True),
        sa.Column("x_carbon_method", sa.String(length=32), nullable=True),
        sa.Column("x_is_final", sa.Boolean(), nullable=False),
        sa.Column("x_is_estimate", sa.Boolean(), nullable=False),
        sa.Column(
            "x_ingested_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "x_source",
            "provider_name",
            "resource_id",
            "charge_period_start",
            "charge_category",
            name="uq_cost_line_items_identity",
        ),
    )
    op.create_index(
        "ix_cost_line_items_charge_period_start",
        "cost_line_items",
        ["charge_period_start"],
    )
    op.create_index(
        "ix_cost_line_items_project", "cost_line_items", ["project"]
    )
    op.create_index(
        "ix_cost_line_items_period_project",
        "cost_line_items",
        ["billing_period", "project"],
    )

    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day", "currency"),
    )

    op.create_table(
        "cost_centers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(length=100), nullable=False),
        sa.Column("cost_center", sa.String(length=64), nullable=False),
        sa.Column("budget_owner", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_name"),
    )

    op.create_table(
        "month_close",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("billing_period", sa.String(length=7), nullable=False),
        sa.Column("closed_by", sa.String(length=255), nullable=False),
        sa.Column(
            "closed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("billing_period"),
    )

    op.create_table(
        "savings_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("project", sa.String(length=100), nullable=False),
        sa.Column("app", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("avoided_eur", sa.Float(), nullable=False),
        sa.Column("realized_eur", sa.Float(), nullable=False),
        sa.Column("energy_avoided_kwh", sa.Float(), nullable=False),
        sa.Column("co2e_avoided_kg", sa.Float(), nullable=False),
        sa.Column("cloud", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day", "project", "app", "category"),
    )
    op.create_index("ix_savings_ledger_day", "savings_ledger", ["day"])


def downgrade() -> None:
    op.drop_index("ix_savings_ledger_day", table_name="savings_ledger")
    op.drop_table("savings_ledger")
    op.drop_table("month_close")
    op.drop_table("cost_centers")
    op.drop_table("fx_rates")
    op.drop_index(
        "ix_cost_line_items_period_project", table_name="cost_line_items"
    )
    op.drop_index("ix_cost_line_items_project", table_name="cost_line_items")
    op.drop_index(
        "ix_cost_line_items_charge_period_start", table_name="cost_line_items"
    )
    op.drop_table("cost_line_items")
