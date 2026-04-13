"""migrate_to_terraform_based_deployments

Revision ID: b9751e077ee4
Revises: eba979457564
Create Date: 2026-04-07 14:25:36.222758

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9751e077ee4'
down_revision: Union[str, Sequence[str], None] = 'eba979457564'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema to support Terraform-based deployments."""
    # Remove old OpenStack/AWS specific columns
    with op.batch_alter_table('deployments') as batch_op:
        batch_op.drop_column('os_vm_db1_id')
        batch_op.drop_column('os_vm_db2_id')
        batch_op.drop_column('os_vm_db1_ip')
        batch_op.drop_column('os_vm_db2_ip')
        batch_op.drop_column('aws_asg_name')
        batch_op.drop_column('aws_alb_dns')
        batch_op.drop_column('aws_instance_ids')

        # Add new Terraform-related columns
        batch_op.add_column(sa.Column('terraform_outputs', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('terraform_state_path', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('resource_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('template_name', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('template_icon', sa.String(50), nullable=True))
        batch_op.add_column(sa.Column('template_category', sa.String(50), nullable=True))


def downgrade() -> None:
    """Downgrade schema back to old SAGA pattern."""
    with op.batch_alter_table('deployments') as batch_op:
        # Remove Terraform columns
        batch_op.drop_column('terraform_outputs')
        batch_op.drop_column('terraform_state_path')
        batch_op.drop_column('resource_count')
        batch_op.drop_column('template_name')
        batch_op.drop_column('template_icon')
        batch_op.drop_column('template_category')

        # Restore old columns
        batch_op.add_column(sa.Column('os_vm_db1_id', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('os_vm_db2_id', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('os_vm_db1_ip', sa.String(50), nullable=True))
        batch_op.add_column(sa.Column('os_vm_db2_ip', sa.String(50), nullable=True))
        batch_op.add_column(sa.Column('aws_asg_name', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('aws_alb_dns', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('aws_instance_ids', sa.Text(), nullable=True))
