"""initial schema — 5 core tables (Phase A)

Revision ID: 0001
Revises:
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ms_oid", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text()),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_users_email", "users", ["email"])

    # meetings
    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text()),
        sa.Column("purpose", sa.Text()),
        sa.Column("venue", sa.Text()),
        sa.Column("date", sa.Date()),
        sa.Column("chaired_by", sa.Text()),
        sa.Column("noted_by", sa.Text()),
        sa.Column("attendees", postgresql.JSONB()),
        sa.Column("mom_json", postgresql.JSONB()),
        sa.Column("status", sa.Text(), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_meetings_user_date", "meetings", ["user_id", "date"])

    # meeting_members
    op.create_table(
        "meeting_members",
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("invited_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("meeting_id", "user_id"),
        sa.CheckConstraint("role IN ('owner', 'editor', 'viewer')", name="ck_members_role"),
    )
    op.create_index("idx_members_meeting", "meeting_members", ["meeting_id"])

    # recordings
    op.create_table(
        "recordings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_label", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_sec", sa.Integer()),
        sa.Column("status", sa.Text(), server_default="recording"),
        sa.Column("audio_path", sa.Text()),
    )
    op.create_index("idx_recordings_meeting_time", "recordings", ["meeting_id", "started_at"])

    # transcript_segments
    op.create_table(
        "transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("recording_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("start_time_ms", sa.Integer()),
        sa.Column("end_time_ms", sa.Integer()),
        sa.Column("speaker", sa.Text()),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("edited_text", sa.Text()),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column("edited_by", postgresql.UUID(as_uuid=True)),
        sa.Column("is_deleted", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_segments_recording_seq",
        "transcript_segments",
        ["recording_id", "seq"],
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_table("transcript_segments")
    op.drop_table("recordings")
    op.drop_table("meeting_members")
    op.drop_table("meetings")
    op.drop_table("users")
