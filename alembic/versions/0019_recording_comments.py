"""Recording comments — Notta-style per-recording threaded notes.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-15

Adds a `recording_comments` table for the Comments side pane. Each row
is a user-authored note anchored optionally to a playback position
(`anchor_ms`) and optionally to a specific transcript segment
(`segment_seq`). Soft-deletable via `deleted_at` to keep history
intact when a user removes their comment.

The FE CommentsPane reads via /api/recordings/{id}/comments and
renders the list sorted by anchor_ms ascending. Clicking a comment
seeks the audio player to anchor_ms. Editing/removing requires the
comment to belong to the current user.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recording_comments",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "recording_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        # Position in audio (ms) the comment anchors to. NULL = general
        # comment on the recording with no specific position.
        sa.Column("anchor_ms", sa.Integer(), nullable=True),
        # Optional: also pin to a specific transcript_segments.seq so the
        # comment can highlight a particular row in the Notta view.
        sa.Column("segment_seq", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_recording_comments_recording_anchor",
        "recording_comments",
        ["recording_id", "anchor_ms"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_recording_comments_recording_anchor", table_name="recording_comments")
    op.drop_table("recording_comments")
