"""Direct GitHub OAuth grants and single-use states; join current migration heads."""

from alembic import op
import sqlalchemy as sa

revision = "b82d04c71a96"
down_revision = ("e4a7c2d9f1b3", "f71a0c9e2b46")
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "github_oauth_credentials",
        sa.Column("user_sub", sa.String(255), primary_key=True),
        sa.Column("github_user_id", sa.String(32), nullable=False),
        sa.Column("github_login", sa.String(39), nullable=False),
        sa.Column("credentials_ciphertext", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(), nullable=False),
        sa.Column("refreshing_until", sa.DateTime()),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "github_oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("user_sub", sa.String(255), nullable=False, index=True),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime()),
    )


def downgrade():
    op.drop_table("github_oauth_states")
    op.drop_table("github_oauth_credentials")
