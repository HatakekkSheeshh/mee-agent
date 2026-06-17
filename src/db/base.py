"""
SQLAlchemy 2 async setup — engine, session factory, declarative Base.

Usage in FastAPI:
    from meeting.db import get_session
    @app.get("/...")
    async def handler(session: AsyncSession = Depends(get_session)):
        ...
"""
import os
from typing import AsyncIterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL env var is required. "
        "Format: postgresql://user:pass@host:port/db (driver auto-added)"
    )


def _to_async_url(url: str) -> str:
    """Normalize URL to use asyncpg driver. Accepts plain postgres://, postgresql://, or postgresql+asyncpg://."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql+"):
        # Other driver explicitly specified — replace with asyncpg
        return "postgresql+asyncpg://" + url.split("://", 1)[1]
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):  # Heroku/Render style
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


ASYNC_DATABASE_URL = _to_async_url(DATABASE_URL)

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ─── Sync engine (for Celery tasks) ─────────────────────────────────
#
# Celery workers run in a sync world. Using async SQLAlchemy from inside
# Celery means wrapping every call in `asyncio.run()`, which creates a
# fresh event loop per task. The async connection pool's connections
# get bound to that loop and become unusable for the next task — leading
# to the recurring "Future attached to a different event loop" errors.
#
# This sync engine sidesteps the issue entirely: psycopg2 is fully
# sync, no event loop involvement, connections in the pool can be
# reused freely across Celery tasks. FastAPI keeps using the async
# engine above for HTTP concurrency.

def _to_sync_url(url: str) -> str:
    """Strip any async driver hint so SQLAlchemy picks psycopg2."""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


SYNC_DATABASE_URL = _to_sync_url(DATABASE_URL)

sync_engine = create_engine(
    SYNC_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
)
