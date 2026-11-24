"""activity_events: drop the source index, add a covering window index

Revision ID: e5a2c8f4b1d7
Revises: d3b8f1a6c2e9
Create Date: 2026-11-24 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e5a2c8f4b1d7"
down_revision: Union[str, Sequence[str], None] = "d3b8f1a6c2e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [
    "time",
    "source",
    "is_read",
    "project",
    "actor",
    "action",
    "notable",
]


def upgrade() -> None:
    op.drop_index("ix_activity_events_source", table_name="activity_events")
    op.create_index("ix_activity_events_window", "activity_events", COLUMNS)


def downgrade() -> None:
    op.drop_index("ix_activity_events_window", table_name="activity_events")
    op.create_index("ix_activity_events_source", "activity_events", ["source"])
