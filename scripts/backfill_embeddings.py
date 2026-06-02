"""One-shot backfill: embed all memory_events rows where embedding IS NULL.

Usage:
    .venv/bin/python scripts/backfill_embeddings.py

Processes in batches of 32 (bge-m3 supports batching). Commits per batch so
partial progress survives if interrupted.
"""
import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv(override=True, interpolate=False)

from sqlalchemy import select, update
from meeting.db.base import AsyncSessionLocal, async_engine
from meeting.db.models import MemoryEventRow
from meeting.services.embedding import embed_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 32


async def main():
    total_done = 0
    total_failed = 0

    async with AsyncSessionLocal() as session:
        # Count first
        all_rows = (await session.execute(
            select(MemoryEventRow).where(MemoryEventRow.embedding.is_(None))
        )).scalars().all()
        total = len(all_rows)
        logger.info(f"Found {total} rows without embedding")

        if total == 0:
            logger.info("Nothing to backfill — exit.")
            return

        # Process in batches
        for batch_start in range(0, total, BATCH_SIZE):
            batch = all_rows[batch_start:batch_start + BATCH_SIZE]
            texts = [r.text for r in batch]
            logger.info(
                f"Batch {batch_start // BATCH_SIZE + 1}/"
                f"{(total + BATCH_SIZE - 1) // BATCH_SIZE} → "
                f"embedding {len(batch)} texts..."
            )
            embeddings = embed_batch(texts)

            for row, emb in zip(batch, embeddings):
                if emb is None:
                    total_failed += 1
                    continue
                row.embedding = emb
                total_done += 1

            await session.commit()
            logger.info(f"  Committed batch ({total_done} done, {total_failed} failed)")

    await async_engine.dispose()
    logger.info(f"Backfill complete: {total_done} embedded, {total_failed} failed out of {total}")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
