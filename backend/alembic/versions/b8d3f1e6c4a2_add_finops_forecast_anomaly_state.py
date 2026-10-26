"""add finops forecast and anomaly alert state

Revision ID: b8d3f1e6c4a2
Revises: d8f3a6c1e4b7
Create Date: 2026-10-26 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b8d3f1e6c4a2"
down_revision: Union[str, Sequence[str], None] = "d8f3a6c1e4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cost_alerts", sa.Column("subject_day", sa.Date(), nullable=True)
    )
    op.create_index(
        "ix_cost_alerts_kind_app_day",
        "cost_alerts",
        ["kind", "app_id", "subject_day"],
    )
    op.add_column(
        "budget_alert_states",
        sa.Column(
            "forecast_alerted_month", sa.String(length=7), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("budget_alert_states", "forecast_alerted_month")
    op.drop_index("ix_cost_alerts_kind_app_day", table_name="cost_alerts")
    op.drop_column("cost_alerts", "subject_day")
