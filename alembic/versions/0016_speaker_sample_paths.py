"""Per-cluster speaker voice sample paths

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-08

Adds `recordings.speaker_sample_paths` — JSONB map from pyannote cluster id
to the filesystem path of a 3s WAV clip representing that speaker. Lets
SpeakerMapper play a sample so the user can verify the cluster → name
mapping before saving a voiceprint.

Shape: {"SPEAKER_00": "output/<recording_id>/spk_SPEAKER_00.wav", ...}

Populated by /api/recordings/{id}/diarize-result when the diarize payload
includes sample_audio_b64 (base64-encoded WAVs produced by local_diarize).
NULL = pre-0016 recordings or recordings whose pyannote run didn't extract
samples (PhoWhisper path doesn't yet ship samples — fine, SpeakerMapper
just hides the play button for clusters not in this map).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column(
            "speaker_sample_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("recordings", "speaker_sample_paths")
