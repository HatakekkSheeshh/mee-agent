"""MoM two-level — per-recording MoM + project summary

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-31

Changes:
    - ADD recordings.mom_json (JSONB nullable) — per-recording MoM (biên bản phiên họp)
    - ADD meetings.project_summary_json (JSONB nullable) — aggregated timeline decisions
    - DROP meetings.mom_json — legacy column, replaced by recording-level + summary
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("mom_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "meetings",
        sa.Column(
            "project_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    # Drop legacy meeting-level MoM column (hackathon scope, no backward compat).
    op.drop_column("meetings", "mom_json")


def downgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("mom_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_column("meetings", "project_summary_json")
    op.drop_column("recordings", "mom_json")
