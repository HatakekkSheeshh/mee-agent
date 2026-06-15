"""roles pool — role-persona kickoff catalog + seed

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-14

Creates the `roles` table (the authoritative, enumerable role-persona pool that
drives Mee's proactive chat kickoff) and seeds the 10 company roles from
meeting.db.seed_roles. The seed is idempotent (ON CONFLICT (name) DO NOTHING)
so it's safe to re-apply against the shared DB that is stamped past head and run
without `alembic upgrade head` (see the spec's Migration note +
memory db-alembic-drift-remote-ahead).
"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from meeting.db.seed_roles import SEED_ROLES

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data_plan", sa.Text(), nullable=False, server_default="minimal"),
        sa.Column("kickoff_prompt", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    insert = sa.text(
        "INSERT INTO roles (id, name, description, data_plan, kickoff_prompt) "
        "VALUES (:id, :name, :description, :data_plan, :kickoff_prompt) "
        "ON CONFLICT (name) DO NOTHING"
    )
    bind = op.get_bind()
    for r in SEED_ROLES:
        bind.execute(
            insert,
            {
                "id": str(uuid.uuid4()),
                "name": r["name"],
                "description": r["description"],
                "data_plan": r["data_plan"],
                "kickoff_prompt": r["kickoff_prompt"],
            },
        )


def downgrade() -> None:
    op.drop_table("roles")
