"""STT + LLM model selection per recording/meeting

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-03

Adds optional `stt_model` + `llm_model` text columns to both `meetings` and
`recordings`. Recording-level overrides meeting-level (project default).
NULL = inherit (falls back to DEFAULT_STT/DEFAULT_LLM in model_registry).

Logical IDs stored: "whisper" / "phowhisper" (STT) and
"gemma" / "qwen" / "gpt-oss" (LLM). The model_registry module maps these to
actual base_url / api_key / model name at runtime via env vars.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("stt_model", sa.Text(), nullable=True))
    op.add_column("meetings", sa.Column("llm_model", sa.Text(), nullable=True))
    op.add_column("recordings", sa.Column("stt_model", sa.Text(), nullable=True))
    op.add_column("recordings", sa.Column("llm_model", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recordings", "llm_model")
    op.drop_column("recordings", "stt_model")
    op.drop_column("meetings", "llm_model")
    op.drop_column("meetings", "stt_model")
