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
import pathlib
import sys
from collections.abc import Callable
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# load_dotenv must precede project imports
from dotenv import load_dotenv
load_dotenv(override=True, interpolate=False)

from sqlalchemy import select

from meeting.db.base import AsyncSessionLocal, async_engine
from meeting.db import models, repositories as repo
from meeting.services.role_mapping import classify_role, resolve_role
from meeting.graphs._chat_llm import _llm_client, _llm_model

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("classify_roles")


def _make_generate() -> Callable[[list[dict[str, str]]], str]:
    client = _llm_client()
    model = _llm_model()

    def generate(messages: list[dict[str, str]]) -> str:
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.0, max_tokens=120
        )
        return resp.choices[0].message.content or ""

    return generate


async def run(dry_run: bool = False) -> dict[str, int]:
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
    logger.info("done: %s", stats)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Classify unmatched users into the role pool.")
    ap.add_argument("--dry-run", action="store_true", help="log decisions, write nothing")
    args = ap.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
