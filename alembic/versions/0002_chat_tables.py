"""chat tables — chat_sessions, chat_messages, pending_actions, audit_log (Phase B2)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # chat_sessions — 1 user × 1 meeting (optional) = 1 conversation thread
    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text()),  # auto-generated từ first message
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_chat_sessions_user", "chat_sessions", ["user_id", "last_activity_at"])
    op.create_index("idx_chat_sessions_meeting", "chat_sessions", ["meeting_id"])

    # chat_messages — message history
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),  # 'user' | 'agent' | 'tool' | 'system'
        sa.Column("content", postgresql.JSONB(), nullable=False),  # {text, tool_call?, ...}
        sa.Column("metadata", postgresql.JSONB()),  # extra: tokens, latency, model
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user', 'agent', 'tool', 'system')", name="ck_messages_role"),
    )
    op.create_index("idx_chat_messages_session_time", "chat_messages", ["session_id", "created_at"])

    # pending_actions — actions waiting for HITL approval
    op.create_table(
        "pending_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),  # LangGraph thread_id để resume
        sa.Column("checkpoint_id", sa.Text()),  # specific checkpoint to resume from
        sa.Column("tool_name", sa.Text(), nullable=False),  # 'send_email', 'create_task'...
        sa.Column("tool_args", postgresql.JSONB(), nullable=False),  # {to, subject, body}
        sa.Column("rationale", sa.Text()),  # vì sao agent đề xuất action này
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", postgresql.JSONB()),  # {action: approve/reject/edit, edited_args?, reason?}
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executed', 'failed')",
            name="ck_pending_actions_status",
        ),
    )
    op.create_index("idx_pending_actions_user_pending", "pending_actions",
                    ["user_id", "status"], postgresql_where=sa.text("status = 'pending'"))
    op.create_index("idx_pending_actions_session", "pending_actions", ["session_id"])

    # audit_log — every side-effect action (for compliance + debug)
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_sessions.id", ondelete="SET NULL")),
        sa.Column("action_type", sa.Text(), nullable=False),  # 'tool_execute', 'tool_reject', ...
        sa.Column("tool_name", sa.Text()),
        sa.Column("tool_args", postgresql.JSONB()),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_msg", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_audit_log_user_time", "audit_log", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("pending_actions")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
