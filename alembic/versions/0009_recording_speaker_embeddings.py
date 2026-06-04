"""recordings.speaker_embeddings — per-cluster voice vectors

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-01

Stores the 256-dim cluster embeddings returned by phowhisper-server when an
audio file is uploaded, so the Clean step can later match them against the
user's voiceprints DB without re-running diarization.

Shape: {"SPEAKER_00": [...256 floats...], "SPEAKER_01": [...]}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column(
            "speaker_embeddings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("recordings", "speaker_embeddings")
