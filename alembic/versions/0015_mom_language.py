"""MoM language picker per recording/meeting

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-04

Adds optional `mom_language` text columns to both `meetings` and `recordings`
so user can override MoM generation language ("vi" / "en"). NULL = inherit
(recording → meeting → UI language → "vi" default).

Hardcoded prompt in note_generator.py was always Vietnamese; this lets a
recording produced from an English meeting generate an English MoM.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("mom_language", sa.Text(), nullable=True))
    op.add_column("recordings", sa.Column("mom_language", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recordings", "mom_language")
    op.drop_column("meetings", "mom_language")
