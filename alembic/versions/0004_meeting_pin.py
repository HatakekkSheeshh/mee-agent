"""meetings.is_pinned column (Phase E5: sidebar context menu)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "idx_meetings_pinned",
        "meetings",
        ["is_pinned", "created_at"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("idx_meetings_pinned", table_name="meetings")
    op.drop_column("meetings", "is_pinned")
