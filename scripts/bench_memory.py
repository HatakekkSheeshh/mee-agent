"""Benchmark: agent-memory (AgentBase distilled record) vs Postgres (raw data).

For each (meeting, prompt) in a YAML prompt set, measure BOTH sources on:
  - latency:  fetch-only  AND end-to-end (fetch + an LLM answer);
  - relevance: bge-m3 cosine of the prompt vs each source (prompt↔mem, prompt↔pg);
  - fidelity:  cosine of the distilled record vs the full Postgres text (mem↔pg).

Then print + write a Markdown report and a JSON dump for an overall view of the
fast-but-lossy (agent mem) vs complete-but-heavy (Postgres) tradeoff.

The number-crunching is the pure core in meeting/services/memory_bench.py; this
script is the impure shell (DB + AgentBase browse + embedding + LLM I/O + timing).

Scope (2026-06-12): only the `project_facts` projection exists — no user_pref /
custom-fact memory yet — so this compares that single record against Postgres.

Usage:
    venv/bin/python scripts/bench_memory.py
    venv/bin/python scripts/bench_memory.py --prompts bench/memory_prompts.yaml
    venv/bin/python scripts/bench_memory.py --no-llm        # skip e2e LLM (fetch+sim only, fast)
    venv/bin/python scripts/bench_memory.py --out output/memory_bench
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(override=True, interpolate=False)

import yaml
from sqlalchemy import select

from meeting.db import repositories as repo
from meeting.db.base import AsyncSessionLocal, async_engine
from meeting.db.models import Meeting
from meeting.graphs._chat_llm import _llm_client, _llm_model
from meeting.memory_client import search_project_record, strip_project_marker
from meeting.services.embedding import embed_batch
from meeting.services.memory_bench import (
    BenchRow,
    best_sim,
    build_postgres_chunks,
    cosine,
    postgres_whole_text,
    render_markdown,
    summarize_rows,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bench_memory")

ANSWER_SYSTEM = (
    "Bạn là trợ lý cuộc họp Mee. CHỈ dựa vào DỮ LIỆU được cung cấp để trả lời ngắn "
    "gọn bằng tiếng Việt, không bịa. Nếu dữ liệu không có, nói chưa có thông tin."
)


def _timed_ms(fn):
    """Run fn(), return (result, elapsed_ms) using a monotonic clock."""
    t0 = time.perf_counter()
    res = fn()
    return res, (time.perf_counter() - t0) * 1000.0


async def _timed_ms_async(coro_fn):
    """Await coro_fn(), return (result, elapsed_ms). For timing the real DB query."""
    t0 = time.perf_counter()
    res = await coro_fn()
    return res, (time.perf_counter() - t0) * 1000.0


def _llm_answer(source_text: str, prompt: str) -> str:
    """One-shot grounded answer from a single source (mirrors the chat agent's
    'answer only from provided data' contract)."""
    client = _llm_client()
    resp = client.chat.completions.create(
        model=_llm_model(),
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"DỮ LIỆU:\n{source_text or '(trống)'}\n\nCÂU HỎI: {prompt}"},
        ],
        max_tokens=512,
        timeout=60,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return (resp.choices[0].message.content or "").strip()


async def _resolve_meeting_id(session, ref: str):
    """Map a prompt-set 'meeting' (UUID or title ILIKE) → meeting id (UUID) or None.

    ID resolution only — the actual data-loading query (get_meeting w/ selectinload)
    is timed separately as the Postgres FETCH, so it's apples-to-apples with the
    AgentBase network browse. Resolving the human ref → id is shared by both sources.
    """
    try:
        return uuid.UUID(str(ref))
    except (ValueError, AttributeError):
        return (await session.execute(
            select(Meeting.id).where(Meeting.title.ilike(f"%{ref}%"), Meeting.deleted_at.is_(None))
            .order_by(Meeting.created_at.desc()).limit(1)
        )).scalars().first()


def _meeting_recordings(meeting) -> list[dict]:
    """Sorted (started_at, id) recordings → [{label, date, mom}] for chunking."""
    recs = sorted((meeting.recordings or []), key=repo.recording_sort_key)
    return [
        {
            "label": r.title or r.session_label or "Phiên họp",
            "date": r.started_at.date().isoformat() if r.started_at else None,
            "mom": r.mom_json,
        }
        for r in recs
    ]


async def _bench_one(session, entry: dict, *, use_llm: bool) -> BenchRow | None:
    ref = entry.get("meeting")
    prompt = entry.get("prompt", "")
    mid_uuid = await _resolve_meeting_id(session, ref)
    if not mid_uuid:
        logger.warning(f"meeting {ref!r} not found — skipping prompt {prompt!r}")
        return None
    mid = str(mid_uuid)
    # Drop any cached ORM state so the timed get_meeting below issues real SQL
    # (the identity map would otherwise let a re-query skip the round-trip).
    session.expire_all()

    # ── fetch: agent memory (AgentBase network browse) ──
    rec, mem_fetch_ms = _timed_ms(lambda: search_project_record(mid))
    mem_text = strip_project_marker(rec.get("memory")) if rec else ""

    # ── fetch: Postgres — the REAL data-loading query (get_meeting w/ selectinload)
    #    + chunk assembly, timed together so it's comparable to the AgentBase browse.
    async def _pg_fetch():
        m = await repo.get_meeting(session, mid_uuid)
        chunks = build_postgres_chunks(m.project_summary_json, _meeting_recordings(m))
        return m, chunks, postgres_whole_text(chunks)
    (meeting, pg_chunks, pg_whole), pg_fetch_ms = await _timed_ms_async(_pg_fetch)
    if not meeting:
        logger.warning(f"meeting {ref!r} vanished between resolve and load — skipping")
        return None
    label = meeting.title or mid

    # ── relevance + fidelity via bge-m3 (one batched embedding call) ──
    chunk_texts = [c["text"] for c in pg_chunks]
    vecs = embed_batch([prompt, mem_text or " ", pg_whole or " ", *chunk_texts])
    q_vec, mem_vec, pg_vec = vecs[0], vecs[1], vecs[2]
    chunk_vecs = vecs[3:]
    sim_q_mem = cosine(q_vec, mem_vec) if mem_text else 0.0
    sim_q_pg = best_sim(q_vec, chunk_vecs)
    sim_mem_pg = cosine(mem_vec, pg_vec) if (mem_text and pg_whole) else 0.0

    # ── end-to-end: fetch + an LLM answer from each source ──
    mem_answer = pg_answer = ""
    mem_e2e_ms, pg_e2e_ms = mem_fetch_ms, pg_fetch_ms
    if use_llm:
        mem_answer, mem_llm_ms = _timed_ms(lambda: _llm_answer(mem_text, prompt))
        pg_answer, pg_llm_ms = _timed_ms(lambda: _llm_answer(pg_whole, prompt))
        mem_e2e_ms = mem_fetch_ms + mem_llm_ms
        pg_e2e_ms = pg_fetch_ms + pg_llm_ms

    logger.info(
        f"{label} | {prompt!r}: fetch mem={mem_fetch_ms:.0f}ms pg={pg_fetch_ms:.0f}ms | "
        f"sim q↔mem={sim_q_mem:.3f} q↔pg={sim_q_pg:.3f} mem↔pg={sim_mem_pg:.3f}"
    )
    return BenchRow(
        meeting=label, prompt=prompt,
        mem_fetch_ms=mem_fetch_ms, mem_e2e_ms=mem_e2e_ms,
        pg_fetch_ms=pg_fetch_ms, pg_e2e_ms=pg_e2e_ms,
        mem_chars=len(mem_text), pg_chars=len(pg_whole),
        sim_q_mem=sim_q_mem, sim_q_pg=sim_q_pg, sim_mem_pg=sim_mem_pg,
        mem_answer=mem_answer, pg_answer=pg_answer, note=entry.get("note", ""),
    )


async def main(prompts_path: str, out_base: str, use_llm: bool, limit: int | None) -> int:
    entries = yaml.safe_load(pathlib.Path(prompts_path).read_text(encoding="utf-8")) or []
    if limit:
        entries = entries[:limit]
    logger.info(f"{len(entries)} prompt(s) from {prompts_path}{'' if use_llm else ' (--no-llm)'}")

    rows: list[BenchRow] = []
    async with AsyncSessionLocal() as session:
        for entry in entries:
            try:
                row = await _bench_one(session, entry, use_llm=use_llm)
            except Exception as e:  # noqa: BLE001 — one prompt must not abort the run
                logger.error(f"prompt {entry.get('prompt')!r} FAILED: {e}")
                continue
            if row:
                rows.append(row)
    await async_engine.dispose()

    if not rows:
        logger.warning("no rows produced — check meeting refs / MEMORY_ID / embedding env")
        return 1

    summary = summarize_rows(rows)
    report_md = render_markdown(rows, summary)

    out_md = pathlib.Path(f"{out_base}.md")
    out_json = pathlib.Path(f"{out_base}.json")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(report_md, encoding="utf-8")
    out_json.write_text(
        json.dumps(
            {"summary": summary, "rows": [vars(r) for r in rows]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + report_md)
    logger.info(f"Report → {out_md}  |  JSON → {out_json}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Benchmark agent-memory vs Postgres retrieval")
    ap.add_argument("--prompts", default="bench/memory_prompts.yaml",
                    help="YAML prompt set (default: bench/memory_prompts.yaml)")
    ap.add_argument("--out", default="output/memory_bench",
                    help="Output base path; writes <out>.md and <out>.json")
    ap.add_argument("--no-llm", action="store_true",
                    help="Skip the end-to-end LLM answer; measure fetch latency + similarity only.")
    ap.add_argument("--limit", type=int, default=None, help="Only run the first N prompts.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.prompts, args.out, not args.no_llm, args.limit)) or 0)
