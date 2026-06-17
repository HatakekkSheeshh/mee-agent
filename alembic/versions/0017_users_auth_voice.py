"""Users auth + voice enrollment columns.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-11

Extends the `users` table for mock + real O365 authentication and the
post-login voice enrollment step:

  - avatar_url       — Microsoft Graph photo URL or null
  - ms_tenant_id     — Microsoft Azure tenant id (oid lives in ms_oid already)
  - voice_enrolled   — true once user records the enrollment phrase
                       (matches voiceprints row via user_id + label="enrollment")
  - ms_oid           — relaxed to nullable so MockProvider can create users
                       without a real Microsoft object id
  - email            — added unique constraint so OAuth callback can dedupe
                       returning users by email
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("ms_tenant_id", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "voice_enrolled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Relax ms_oid to nullable so MockProvider doesn't need to fake one.
    op.alter_column("users", "ms_oid", existing_type=sa.Text(), nullable=True)
    # Email becomes the dedupe key across both mock + real auth providers.
    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.alter_column("users", "ms_oid", existing_type=sa.Text(), nullable=False)
    op.drop_column("users", "voice_enrolled")
    op.drop_column("users", "ms_tenant_id")
    op.drop_column("users", "avatar_url")
