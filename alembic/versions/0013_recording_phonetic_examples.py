"""Recording phonetic examples cache

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-03

Adds `recordings.phonetic_examples_json` — LLM-generated phonetic mappings
from the recording's vocab_hints. Format:
  {
    "mappings": [{"wrong": "công vô lu sần", "correct": "convolution"}, ...],
    "generated_for_vocab": "<sha256 of vocab_hints string at generation time>",
    "generated_at_ms": 1717400000000
  }

The cleaner LLM injects `mappings` into its prompt to teach phonetic rewrite
patterns specific to each meeting's terminology. Avoids hardcoding examples
in the prompt template — every domain (ML, devops, healthcare, legal) gets
auto-generated phonetic hints.

Cached so we don't regenerate on every clean call. Invalidated when
vocab_hints changes (compared via the embedded sha256).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column(
            "phonetic_examples_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("recordings", "phonetic_examples_json")
