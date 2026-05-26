"""
Alembic env — sync mode using psycopg2 (DATABASE_URL_SYNC).

Migrations use sync driver while the app uses asyncpg. Same DB, two drivers.
"""
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

load_dotenv()

# Import models so Base.metadata is populated
from meeting.db.base import Base  # noqa: E402
from meeting.db import models  # noqa: E402, F401

config = context.config

# Override sqlalchemy.url from environment (fail-fast if not set)
db_url = os.getenv("DATABASE_URL_SYNC")
if not db_url:
    raise RuntimeError(
        "DATABASE_URL_SYNC env var is required. "
        "See .env.example for format: postgresql+psycopg2://user:pass@host:port/db"
    )
config.set_main_option("sqlalchemy.url", db_url)

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
