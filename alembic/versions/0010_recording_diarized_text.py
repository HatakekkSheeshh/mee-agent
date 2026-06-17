"""recordings.diarized_text — PhoWhisper diarized transcript with speaker tags

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-02

After live record stops, post_record_diarize sends the full buffered audio to
PhoWhisper which returns segments tagged with SPEAKER_NN. We persist those
segments concatenated as "SPEAKER_00: text\nSPEAKER_01: text\n…" so the
cleaner LLM can use it as authoritative source (instead of the untagged MaaS
Whisper text accumulated during streaming).

For file uploads this column is also useful as a backup — the same PhoWhisper
output is the authoritative version of the transcript when it exists.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("diarized_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recordings", "diarized_text")
