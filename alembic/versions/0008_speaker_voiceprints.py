"""speaker_voiceprints — per-user voiceprint dictionary for zero-shot speaker ID

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-01

Stores a 256-dim pyannote embedding per known speaker so the matcher can
recognise returning speakers across meetings (Approach D / E of speaker ID).

Flow:
    1. User edits "SPEAKER_00" → "Linh" in CleanEditor
    2. FE POSTs the cluster embedding + name to /api/voiceprints
    3. Next meeting, matcher computes cosine similarity vs this row,
       pre-maps SPEAKER_NN → "Linh" if similarity > threshold (0.7)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "speaker_voiceprints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False,
                  server_default="1"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_voiceprints_user_name",
        "speaker_voiceprints",
        ["user_id", "name"],
        unique=True,
    )

    # Switch embedding to pgvector type with IVFFlat cosine index. We declare
    # the column as Float[] first (line above) so the migration runs on plain
    # Postgres, then upgrade to vector(256) here — pgvector ext must be on.
    op.execute("ALTER TABLE speaker_voiceprints "
               "ALTER COLUMN embedding TYPE vector(256) "
               "USING embedding::vector(256)")
    op.execute("CREATE INDEX idx_voiceprints_embedding "
               "ON speaker_voiceprints "
               "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50)")


def downgrade() -> None:
    op.drop_index("idx_voiceprints_embedding", table_name="speaker_voiceprints")
    op.drop_index("idx_voiceprints_user_name", table_name="speaker_voiceprints")
    op.drop_table("speaker_voiceprints")
