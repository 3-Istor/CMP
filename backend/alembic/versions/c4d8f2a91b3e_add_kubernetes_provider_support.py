"""add_kubernetes_provider_support

Revision ID: c4d8f2a91b3e
Revises: b9751e077ee4
Create Date: 2026-05-24 10:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d8f2a91b3e'
down_revision: Union[str, Sequence[str], None] = 'b9751e077ee4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Kubernetes provider support to deployments table."""
    with op.batch_alter_table('deployments') as batch_op:
        # Add provider discriminator
        batch_op.add_column(
            sa.Column(
                'provider_type',
                sa.Enum('LEGACY_HYBRID', 'KUBERNETES', name='providertype'),
                nullable=False,
                server_default='LEGACY_HYBRID'
            )
        )

        # Add multi-tenancy field
        batch_op.add_column(
            sa.Column('project_id', sa.String(100), nullable=True)
        )

        # Add Kubernetes GitOps fields
        batch_op.add_column(
            sa.Column('github_repo_url', sa.String(255), nullable=True)
        )
        batch_op.add_column(
            sa.Column('argocd_app_name', sa.String(100), nullable=True)
        )
        batch_op.add_column(
            sa.Column('k8s_namespace', sa.String(100), nullable=True)
        )


def downgrade() -> None:
    """Remove Kubernetes provider support."""
    with op.batch_alter_table('deployments') as batch_op:
        batch_op.drop_column('k8s_namespace')
        batch_op.drop_column('argocd_app_name')
        batch_op.drop_column('github_repo_url')
        batch_op.drop_column('project_id')
        batch_op.drop_column('provider_type')
