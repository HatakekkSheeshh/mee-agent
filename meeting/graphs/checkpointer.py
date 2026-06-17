"""
LangGraph PostgresSaver — global singleton.

Why singleton:
    - AsyncPostgresSaver has its own connection pool
    - We init once at startup, reuse across all graph invocations
    - .setup() is idempotent — safe to call once, creates 3 tables if not exist:
        * checkpoints              — snapshot of state at each node
        * checkpoint_writes        — write-ahead log
        * checkpoint_blobs         — large values stored separately

Why psycopg (v3) instead of asyncpg:
    langgraph-checkpoint-postgres uses psycopg3 internally — not asyncpg.
    We already have psycopg2-binary for Alembic (sync); now we add psycopg3
    (async, came with langgraph install). They coexist fine.

Conn string:
    DATABASE_URL in .env is "postgresql+asyncpg://..." for SQLAlchemy.
    psycopg3 needs plain "postgresql://..." without driver suffix.
    We strip the "+asyncpg" part below.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_pool: Optional[AsyncConnectionPool] = None
_checkpointer: Optional[AsyncPostgresSaver] = None


def _normalize_conn_string() -> str:
    """Convert SQLAlchemy-style URL to plain psycopg3-compatible URL."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL env var is required. See .env.example."
        )
    # psycopg3 wants "postgresql://", not "postgresql+asyncpg://"
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def init_checkpointer() -> AsyncPostgresSaver:
    """Initialize global checkpointer + connection pool. Idempotent."""
    global _pool, _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    conn_str = _normalize_conn_string()
    logger.info(
        f"Initializing PostgresSaver with conn_string: "
        f"{conn_str.split('@')[1] if '@' in conn_str else conn_str}"
    )

    _pool = AsyncConnectionPool(
        conninfo=conn_str,
        max_size=10,
        min_size=1,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await _pool.open(wait=True)

    _checkpointer = AsyncPostgresSaver(_pool)
    await _checkpointer.setup()  # Creates 3 checkpoint tables if not exist

    logger.info("PostgresSaver ready — checkpoints / writes / blobs tables verified.")
    return _checkpointer


def get_checkpointer() -> AsyncPostgresSaver:
    """Get the global checkpointer. Must call init_checkpointer() first."""
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer not initialized. Call init_checkpointer() on app startup."
        )
    return _checkpointer


async def close_checkpointer() -> None:
    """Close pool on shutdown."""
    global _pool, _checkpointer
    if _pool is not None:
        await _pool.close()
        _pool = None
        _checkpointer = None
        logger.info("PostgresSaver pool closed.")


def reset_checkpointer() -> None:
    """Drop the cached checkpointer + pool references WITHOUT awaiting close().

    Use case: Celery workers re-create the event loop per task. The
    psycopg3 AsyncConnectionPool stored on `_pool` is bound to whatever
    loop first called `init_checkpointer()`. Subsequent tasks on a fresh
    loop must re-init from scratch, but `await _pool.close()` would need
    the original (dead) loop. So we just nuke the references — the
    orphaned pool gets GC'd, OS reclaims the TCP sockets a few seconds
    later. Same `close=False` pattern as SQLAlchemy engine.dispose.
    """
    global _pool, _checkpointer
    _pool = None
    _checkpointer = None
