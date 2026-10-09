"""add_security_dashboard

Revision ID: a7c3e9d1f2b4
Revises: d2e6a1f84c07
Create Date: 2026-10-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e9d1f2b4"
down_revision: Union[str, Sequence[str], None] = "d2e6a1f84c07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Security dashboard tables, and k3s-gitops-app rows typed as Kubernetes."""
    op.create_table(
        "security_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(length=100), nullable=False),
        sa.Column("app", sa.String(length=100), nullable=True),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("rule", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("fix", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("link", sa.String(length=512), nullable=True),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project", "fingerprint", "source"),
    )
    op.create_index(
        "ix_security_findings_project", "security_findings", ["project"]
    )
    op.create_index("ix_security_findings_app", "security_findings", ["app"])

    op.create_table(
        "security_scans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(length=100), nullable=False),
        sa.Column("app", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("collected_at", sa.DateTime(), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=True),
        sa.Column("artifact_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project", "app", "source"),
    )
    op.create_index("ix_security_scans_project", "security_scans", ["project"])

    op.create_table(
        "security_exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(length=100), nullable=False),
        sa.Column("app", sa.String(length=100), nullable=True),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("rule", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("justification", sa.String(length=64), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_security_exceptions_project", "security_exceptions", ["project"]
    )
    op.create_index(
        "ix_security_exceptions_fingerprint",
        "security_exceptions",
        ["fingerprint"],
    )

    op.create_table(
        "security_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("project", sa.String(length=100), nullable=False),
        sa.Column("app", sa.String(length=100), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("grade", sa.String(length=1), nullable=False),
        sa.Column("core", sa.Integer(), nullable=False),
        sa.Column("important", sa.Integer(), nullable=False),
        sa.Column("recommended", sa.Integer(), nullable=False),
        sa.Column("new_major", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day", "project", "app"),
    )
    op.create_index("ix_security_snapshots_day", "security_snapshots", ["day"])
    op.create_index(
        "ix_security_snapshots_project", "security_snapshots", ["project"]
    )

    # The portal never sent provider_type, so every GitOps app was stored as
    # legacy_hybrid and refused by the Kubernetes-only endpoints.
    op.execute(
        "UPDATE deployments SET provider_type = 'KUBERNETES' "
        "WHERE template_id = 'k3s-gitops-app'"
    )


def downgrade() -> None:
    op.drop_table("security_snapshots")
    op.drop_table("security_exceptions")
    op.drop_table("security_scans")
    op.drop_table("security_findings")
