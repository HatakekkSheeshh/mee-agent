"""chat_sessions.meeting_id nullable — user-scoped chat sessions (decoupled from meeting)

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-15

Sessions become user-scoped: a session no longer belongs to one project. The
meeting_id column was created without NOT NULL in 0002, so on a clean DB this is
a no-op. Guarded/idempotent (like 0021) for the drifted prod DB: only alters when
the column is actually NOT NULL. Existing rows keep their (now-ignored) binding.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("chat_sessions"):
        return
    col = {c["name"]: c for c in insp.get_columns("chat_sessions")}.get("meeting_id")
    if col is not None and not col["nullable"]:
        op.alter_column(
            "chat_sessions",
            "meeting_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )


def downgrade() -> None:
    # No-op: re-adding NOT NULL would fail against user-scoped rows whose
    # meeting_id is intentionally NULL. Decoupling is not reversed.
    pass
