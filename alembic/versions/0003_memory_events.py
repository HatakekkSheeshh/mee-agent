"""memory_events table — long-term context across meetings (Sprint A)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    memory_events — semantic events extracted from MoMs for cross-meeting context.

    Examples of events:
        - action_item: "Tuấn deploy v1 trước thứ 5"
        - decision: "Team chốt dùng Postgres thay vì MySQL"
        - commitment: "Linh sẽ review PR #142 trong tuần"
        - blocker: "Database migration blocking deploy"
        - update: "Sprint planning done"
    """
    op.create_table(
        "memory_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),  # 'action_item' | 'decision' | 'commitment' | 'blocker' | 'update' | 'summary'
        sa.Column("topic", sa.Text()),                        # short topic tag for retrieval ('deploy', 'sprint', ...)
        sa.Column("text", sa.Text(), nullable=False),          # the event content
        sa.Column("speaker", sa.Text()),                       # PIC / owner / who said it
        sa.Column("deadline", sa.Text()),                      # optional ISO date string
        sa.Column("event_metadata", postgresql.JSONB()),       # extra fields
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "event_type IN ('action_item', 'decision', 'commitment', 'blocker', 'update', 'summary')",
            name="ck_memory_events_type",
        ),
    )
    op.create_index("idx_memory_events_user_time", "memory_events", ["user_id", "created_at"])
    op.create_index("idx_memory_events_topic", "memory_events", ["user_id", "topic"])
    # Full-text search index for keyword retrieval
    op.execute("""
        CREATE INDEX idx_memory_events_text_fts ON memory_events
        USING GIN (to_tsvector('simple', text))
    """)


def downgrade() -> None:
    op.drop_table("memory_events")
