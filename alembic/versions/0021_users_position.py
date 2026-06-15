"""users.position — raw O365 jobTitle (for background role classification)

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("users")} if insp.has_table("users") else set()
    if "position" not in cols:
        op.add_column("users", sa.Column("position", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "position")
