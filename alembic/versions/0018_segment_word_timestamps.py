"""Per-segment word timestamps for Notta-style word-by-word highlight.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-12

Adds a JSONB `words` column to `transcript_segments`. Populated by STT
backends that return word-level timing (faster-whisper via DTW). NULL when
the STT can't produce them (VNG MaaS Whisper, PhoWhisper with word ts
disabled). FE NottaCleanView reads this for word-accurate playback
highlight; falls back to even-distribute approximation when NULL.

Shape: [{"text": str, "start": float, "end": float}] — absolute seconds.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcript_segments",
        sa.Column("words", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcript_segments", "words")
