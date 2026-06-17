"""
Alembic env — sync mode using psycopg2 (DATABASE_URL_SYNC).

Migrations use sync driver while the app uses asyncpg. Same DB, two drivers.
"""
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

load_dotenv(interpolate=False)

# Import models so Base.metadata is populated
from src.db.base import Base  # noqa: E402
from src.db import models  # noqa: E402, F401

config = context.config


def _to_sync_url(url: str) -> str:
    """Normalize to psycopg2 driver for Alembic (sync)."""
    if url.startswith("postgresql+psycopg2://"):
        return url
    if url.startswith("postgresql+"):
        return "postgresql+psycopg2://" + url.split("://", 1)[1]
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url


# Prefer DATABASE_URL_SYNC if set, otherwise derive from DATABASE_URL
db_url = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError(
        "DATABASE_URL (or DATABASE_URL_SYNC) env var is required."
    )
config.set_main_option("sqlalchemy.url", _to_sync_url(db_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
