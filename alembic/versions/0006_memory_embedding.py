"""memory_events.embedding column (Phase F: hybrid search)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMBED_DIM = 1024  # bge-m3 — change here if swapping embedding model


def upgrade() -> None:
    # pgvector extension already enabled on VDB. Skip CREATE EXTENSION
    # (would need superuser anyway on managed Postgres).
    op.add_column(
        "memory_events",
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
    )
    # IVFFlat index for fast cosine similarity search.
    # `lists` rule of thumb: rows / 1000. Will tune later when data grows.
    op.execute(
        "CREATE INDEX idx_memory_events_embedding "
        "ON memory_events USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memory_events_embedding;")
    op.drop_column("memory_events", "embedding")
