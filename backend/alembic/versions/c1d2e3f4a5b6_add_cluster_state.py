"""add_cluster_state

Revision ID: c1d2e3f4a5b6
Revises: b5e9c2f7a1d8
Create Date: 2026-10-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b5e9c2f7a1d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create cluster_state: lifecycle of the public clusters."""
    op.create_table(
        'cluster_state',
        sa.Column('cloud', sa.String(length=16), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('operation', sa.String(length=8), nullable=True),
        sa.Column('step', sa.String(length=100), nullable=True),
        sa.Column('done', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('started_by', sa.String(length=255), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('log_path', sa.String(length=500), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('cloud'),
    )


def downgrade() -> None:
    op.drop_table('cluster_state')
