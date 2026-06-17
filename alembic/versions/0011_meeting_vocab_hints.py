"""meetings.vocab_hints — technical vocabulary hints

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-02

User-provided comma-separated list of technical terms expected in the meeting
(e.g. "segmentation, convolution, CNN, deploy, API"). Used 2 ways:

1. Whisper prompt bias — `_build_whisper_prompt` includes the list so Whisper
   STT is more likely to transcribe English tech terms correctly instead of
   Vietnamese phonetic mistranscriptions (e.g. "chất manh tây sành" →
   "segmentation").

2. Cleaner LLM post-fix — transcript_cleaner prompt includes the vocab list
   with explicit rule "if you see meaningless Vietnamese phonetic phrases in
   tech context, replace with closest match from this list".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("vocab_hints", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "vocab_hints")
