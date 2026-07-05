"""add_finops_tables

Revision ID: f1a2b3c4d5e6
Revises: 5abfdeaae98a
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '5abfdeaae98a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create FinOps tables: budgets, alerts, recommendation state, alert state."""
    op.create_table(
        'project_budgets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_name', sa.String(length=100), nullable=False),
        sa.Column('monthly_amount_eur', sa.Float(), nullable=False, server_default='0'),
        sa.Column('threshold_warn', sa.Integer(), nullable=True, server_default='70'),
        sa.Column('threshold_critical', sa.Integer(), nullable=True, server_default='90'),
        sa.Column('currency', sa.String(length=8), nullable=True, server_default='EUR'),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_name'),
    )
    op.create_index('ix_project_budgets_project_name', 'project_budgets', ['project_name'])

    op.create_table(
        'cost_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_name', sa.String(length=100), nullable=False),
        sa.Column('app_id', sa.Integer(), nullable=True),
        sa.Column('level', sa.String(length=16), nullable=True, server_default='info'),
        sa.Column('kind', sa.String(length=16), nullable=True, server_default='budget'),
        sa.Column('message', sa.String(length=500), nullable=True, server_default=''),
        sa.Column('value_pct', sa.Float(), nullable=True),
        sa.Column('triggered_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cost_alerts_project_name', 'cost_alerts', ['project_name'])

    op.create_table(
        'recommendation_states',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('rec_id', sa.String(length=64), nullable=False),
        sa.Column('project_name', sa.String(length=100), nullable=False),
        sa.Column('app_id', sa.Integer(), nullable=False),
        sa.Column('rec_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=True, server_default='pending'),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('rec_id'),
    )
    op.create_index('ix_recommendation_states_rec_id', 'recommendation_states', ['rec_id'])
    op.create_index('ix_recommendation_states_project_name', 'recommendation_states', ['project_name'])

    op.create_table(
        'budget_alert_states',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_name', sa.String(length=100), nullable=False),
        sa.Column('last_level', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_name'),
    )
    op.create_index('ix_budget_alert_states_project_name', 'budget_alert_states', ['project_name'])


def downgrade() -> None:
    op.drop_index('ix_budget_alert_states_project_name', table_name='budget_alert_states')
    op.drop_table('budget_alert_states')
    op.drop_index('ix_recommendation_states_project_name', table_name='recommendation_states')
    op.drop_index('ix_recommendation_states_rec_id', table_name='recommendation_states')
    op.drop_table('recommendation_states')
    op.drop_index('ix_cost_alerts_project_name', table_name='cost_alerts')
    op.drop_table('cost_alerts')
    op.drop_index('ix_project_budgets_project_name', table_name='project_budgets')
    op.drop_table('project_budgets')
