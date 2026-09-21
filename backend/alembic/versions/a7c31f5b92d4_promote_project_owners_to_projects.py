"""promote project_owners to projects with target_cloud

Revision ID: a7c31f5b92d4
Revises: f1a2b3c4d5e6
Create Date: 2026-09-15 00:00:00.000000

WS-1 of the multicloud chantier. ``project_owners`` only ever existed through
``Base.metadata.create_all`` — it is in no migration — so this revision creates
``projects`` outright and copies any rows across rather than altering in place.
That also sidesteps SQLite's inability to add a CHECK-constrained column with
ALTER TABLE.

Every pre-existing project is backfilled to ``target_cloud = 'onprem'``, which is
what makes the refactor a no-op for them.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c31f5b92d4"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TARGET_CLOUDS = ("onprem", "aws", "gcp")
PROJECT_STATUSES = ("active", "suspended", "decommissioning")


def upgrade() -> None:
    """Create ``projects``, carry over ``project_owners``, drop the old table.

    ``main.py`` still calls ``Base.metadata.create_all`` on startup, so on a
    running instance ``projects`` may already exist by the time this runs. The
    table creation is therefore conditional, while the row copy and the drop of
    the old table are not — those are the parts that actually migrate data.
    """
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()

    if "projects" not in tables:
        _create_projects_table()

    if "project_owners" in tables:
        op.execute(
            """
            INSERT INTO projects
                (id, project_name, owner_username, target_cloud, status, created_at)
            SELECT id, project_name, owner_username, 'onprem', 'active', created_at
            FROM project_owners
            WHERE project_name NOT IN (SELECT project_name FROM projects)
            """
        )
        op.drop_table("project_owners")


def _create_projects_table() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(length=100), nullable=False),
        sa.Column("owner_username", sa.String(length=255), nullable=False),
        sa.Column(
            "target_cloud",
            sa.Enum(*TARGET_CLOUDS, name="targetcloud"),
            nullable=False,
            server_default="onprem",
        ),
        sa.Column(
            "status",
            sa.Enum(*PROJECT_STATUSES, name="projectstatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_name"),
    )
    op.create_index(
        "ix_projects_project_name", "projects", ["project_name"], unique=True
    )


def downgrade() -> None:
    """Recreate ``project_owners`` and move the rows back, losing the cloud choice."""
    op.create_table(
        "project_owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(length=100), nullable=False),
        sa.Column("owner_username", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_name"),
    )
    op.create_index(
        "ix_project_owners_project_name",
        "project_owners",
        ["project_name"],
        unique=True,
    )

    op.execute(
        """
        INSERT INTO project_owners (id, project_name, owner_username, created_at)
        SELECT id, project_name, owner_username, created_at FROM projects
        """
    )

    op.drop_index("ix_projects_project_name", table_name="projects")
    op.drop_table("projects")
