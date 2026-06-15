"""users.role_id + roles.aliases + alias reseed

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-14

Adds users.role_id (FK→roles.id, nullable — resolved from O365 jobTitle at
login) and roles.aliases (text[], the jobTitle strings that map to each role),
then reseeds aliases from meeting.db.seed_roles by name. The reseed is an UPDATE
by unique name (idempotent).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from meeting.db.seed_roles import SEED_ROLES

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # roles.aliases — text[] default empty.
    op.add_column(
        "roles",
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    # users.role_id — nullable FK → roles.id.
    op.add_column(
        "users",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_role_id", "users", "roles", ["role_id"], ["id"]
    )

    # Reseed aliases by name (idempotent UPDATE).
    update = sa.text("UPDATE roles SET aliases = :aliases WHERE name = :name")
    bind = op.get_bind()
    for r in SEED_ROLES:
        bind.execute(update, {"aliases": r.get("aliases", []), "name": r["name"]})


def downgrade() -> None:
    op.drop_constraint("fk_users_role_id", "users", type_="foreignkey")
    op.drop_column("users", "role_id")
    op.drop_column("roles", "aliases")
