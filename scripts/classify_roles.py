"""Background worker: classify unmatched users' jobTitles into the role pool.

For each user with a `position` but no `role_id`:
  1. deterministic resolve_role(position) first (cheap; a freshly-added alias may match)
  2. miss → classify_role(position) via LLM
  3. confident hit → append position to that role's aliases (self-heal) + backfill role_id

Idempotent + re-runnable. Best-effort per user (one failure is logged, never sinks
the batch). Run: venv/bin/python scripts/classify_roles.py [--dry-run]

See docs/superpowers/specs/2026-06-15-role-autoclassify-design.md.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pathlib
import sys
from collections.abc import Callable
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# load_dotenv must precede project imports
from dotenv import load_dotenv
load_dotenv(override=True, interpolate=False)

from sqlalchemy import select

from src.db.base import AsyncSessionLocal, async_engine
from src.db import models, repositories as repo
from src.services.role_mapping import classify_role, resolve_role
from src.graphs._chat_llm import _llm_client, _llm_model
from src.observability.tracing import init_tracing, shutdown_tracing

# LOG_LEVEL=DEBUG surfaces classify_role's per-decision reasoning (raw answer,
# parsed name + confidence) — useful for tuning the confidence threshold/prompt.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("classify_roles")


def _make_generate() -> Callable[[list[dict[str, str]]], str]:
    client = _llm_client()
    model = _llm_model()

    def generate(messages: list[dict[str, str]]) -> str:
        # max_tokens must be generous: the configured LLM (minimax-m2.5) is a
        # reasoning model that spends tokens on a separate chain-of-thought
        # before emitting the answer in `content`. 120 truncated it mid-think
        # (finish_reason="length", content=null), so it never produced the JSON.
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.0, max_tokens=1024
        )
        msg = resp.choices[0].message
        # Reasoning models put thought in `reasoning`/`reasoning_content` and the
        # final answer in `content`; fall back to reasoning only if content is empty.
        return msg.content or getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""

    return generate


async def run(dry_run: bool = False) -> dict[str, int]:
    # Env-gated (OTEL_ENABLED/LANGFUSE_ENABLED); no-op otherwise. The OpenAI
    # instrumentor it attaches then traces the classify_role LLM calls below.
    init_tracing()
    generate = _make_generate()
    stats = {"scanned": 0, "matched": 0, "classified": 0, "skipped": 0}
    async with AsyncSessionLocal() as session:
        roles = [
            SimpleNamespace(
                id=r.id,
                name=r.name,
                description=r.description,
                data_plan=r.data_plan,
                aliases=list(r.aliases or []),
            )
            for r in await repo.list_roles(session)
        ]
        by_name = {r.name: r for r in roles}
        rows = (
            await session.execute(
                select(models.User.id, models.User.position).where(
                    models.User.position.is_not(None),
                    models.User.role_id.is_(None),
                )
            )
        ).all()
        for user_id, position in rows:
            stats["scanned"] += 1
            try:
                name = resolve_role(position, roles)
                if name:
                    stats["matched"] += 1
                else:
                    name = classify_role(position, roles, generate=generate)
                    if name:
                        stats["classified"] += 1
                if not name:
                    stats["skipped"] += 1
                    logger.info("skip user=%s position=%r (no confident match)", user_id, position)
                    continue
                role = by_name.get(name)
                if role is None:
                    logger.warning("user=%s resolved to %r not in pool map (unexpected)", user_id, name)
                    continue
                if dry_run:
                    logger.info("[dry-run] user=%s %r -> %s", user_id, position, name)
                    continue
                # self-heal: remember this title for next time (deterministic match)
                await repo.add_role_alias(session, role.id, position)
                user = await session.get(models.User, user_id)
                if user is not None:
                    user.role_id = role.id
                await session.commit()
                logger.info("user=%s %r -> %s", user_id, position, name)
            except Exception as e:  # one bad user must not sink the batch
                await session.rollback()
                stats["skipped"] += 1
                logger.warning("classify failed user=%s: %s", user_id, e)
    await async_engine.dispose()
    # Flush BatchSpanProcessor before this short-lived process exits, else
    # batched spans are lost (the FastAPI app flushes on its own; a CLI doesn't).
    shutdown_tracing()
    logger.info("done: %s", stats)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Classify unmatched users into the role pool.")
    ap.add_argument("--dry-run", action="store_true", help="log decisions, write nothing")
    args = ap.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
