"""Per-recording metadata refactor

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-02

Moves "actual meeting event" metadata from project-level (meetings) to
per-recording level. A `meeting` is now a grouping/project mechanism;
each `recording` is the actual meeting event with its own title, date,
attendees, etc.

CHANGES:
  recordings: + title, purpose, date, venue, chaired_by, noted_by,
              attendees (JSONB), vocab_hints (TEXT)
  meetings:   - purpose, venue, date, chaired_by, noted_by, attendees
              (keep: title, vocab_hints, is_pinned)

DATA MIGRATION: copy existing meeting fields to all its recordings before
dropping. After this, the project-level fields cease to exist.

Vocab semantics: vocab_hints on BOTH levels — runtime appends:
   meeting.vocab_hints (project default) + recording.vocab_hints (session override)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add new columns to recordings (all NULLable — fill via data migration)
    op.add_column("recordings", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("recordings", sa.Column("purpose", sa.Text(), nullable=True))
    op.add_column("recordings", sa.Column("date", sa.Date(), nullable=True))
    op.add_column("recordings", sa.Column("venue", sa.Text(), nullable=True))
    op.add_column("recordings", sa.Column("chaired_by", sa.Text(), nullable=True))
    op.add_column("recordings", sa.Column("noted_by", sa.Text(), nullable=True))
    op.add_column(
        "recordings",
        sa.Column("attendees", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("recordings", sa.Column("vocab_hints", sa.Text(), nullable=True))

    # 2. Data migration: copy meeting-level metadata into all its recordings.
    # This preserves existing user data so the UI still shows expected values.
    op.execute("""
        UPDATE recordings r
        SET
            purpose      = m.purpose,
            venue        = m.venue,
            date         = m.date,
            chaired_by   = m.chaired_by,
            noted_by     = m.noted_by,
            attendees    = m.attendees
        FROM meetings m
        WHERE r.meeting_id = m.id
          AND r.purpose IS NULL
          AND r.venue IS NULL
          AND r.date IS NULL
          AND r.chaired_by IS NULL
          AND r.noted_by IS NULL
          AND r.attendees IS NULL
    """)

    # 3. Drop the now-redundant columns from meetings.
    # Index on `date` must go first (column dependency).
    op.drop_index("idx_meetings_user_date", table_name="meetings")
    op.drop_column("meetings", "purpose")
    op.drop_column("meetings", "venue")
    op.drop_column("meetings", "date")
    op.drop_column("meetings", "chaired_by")
    op.drop_column("meetings", "noted_by")
    op.drop_column("meetings", "attendees")
    # Replacement index on (user_id, created_at) for sidebar ordering.
    op.create_index(
        "idx_meetings_user_created", "meetings", ["user_id", "created_at"]
    )


def downgrade() -> None:
    # Re-add meeting-level columns (data NOT restored — best effort).
    op.drop_index("idx_meetings_user_created", table_name="meetings")
    op.add_column("meetings", sa.Column("purpose", sa.Text(), nullable=True))
    op.add_column("meetings", sa.Column("venue", sa.Text(), nullable=True))
    op.add_column("meetings", sa.Column("date", sa.Date(), nullable=True))
    op.add_column("meetings", sa.Column("chaired_by", sa.Text(), nullable=True))
    op.add_column("meetings", sa.Column("noted_by", sa.Text(), nullable=True))
    op.add_column(
        "meetings",
        sa.Column("attendees", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.drop_column("recordings", "vocab_hints")
    op.drop_column("recordings", "attendees")
    op.drop_column("recordings", "noted_by")
    op.drop_column("recordings", "chaired_by")
    op.drop_column("recordings", "venue")
    op.drop_column("recordings", "date")
    op.drop_column("recordings", "purpose")
    op.drop_column("recordings", "title")
    op.create_index("idx_meetings_user_date", "meetings", ["user_id", "date"])
