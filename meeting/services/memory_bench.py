"""Pure metric core for the agent-memory-vs-Postgres retrieval benchmark.

The chat agent grounds Q&A on the DISTILLED AgentBase project record (fast, lossy,
recency-windowed) instead of raw Postgres (complete, heavier). This module turns
measurements into comparable numbers so we can see that tradeoff per prompt and in
aggregate:
  - latency: fetch-only and end-to-end (with an LLM answer) for each source;
  - relevance: bge-m3 cosine of the user prompt vs each source's content;
  - fidelity: cosine of the distilled record vs the full Postgres text.

Everything here is network-free and deterministic. The impure shell that times the
fetches, calls the LLM, and embeds text lives in scripts/bench_memory.py.

Scope note (2026-06-12): only the `project_facts` projection exists today — there is
NO user_pref / custom-fact memory yet, so this benchmarks that one record vs Postgres.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from meeting.services.memory_sync import _mom_texts


@dataclass(frozen=True)
class BenchRow:
    """One (meeting, prompt) measurement across both sources."""
    meeting: str
    prompt: str
    # latency (milliseconds)
    mem_fetch_ms: float
    mem_e2e_ms: float
    pg_fetch_ms: float
    pg_e2e_ms: float
    # retrieved payload size (characters)
    mem_chars: int
    pg_chars: int
    # bge-m3 cosine similarities
    sim_q_mem: float      # prompt ↔ agent-mem text   (relevance)
    sim_q_pg: float       # prompt ↔ best Postgres chunk (relevance)
    sim_mem_pg: float     # agent-mem ↔ full Postgres (distillation fidelity)
    # answer-level scoring — only meaningful when the LLM ran (else 0.0).
    sim_ans_mem_pg: float = 0.0    # answer(mem) ↔ answer(pg): do the sources AGREE?
    sim_ans_mem_gold: float = 0.0  # answer(mem) ↔ gold (when a gold answer is given)
    sim_ans_pg_gold: float = 0.0   # answer(pg)  ↔ gold
    # captured answers (drive the answer section + appendix)
    mem_answer: str = ""
    pg_answer: str = ""
    gold: str = ""
    note: str = ""


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity of two vectors. 0.0 for empty/zero/None inputs."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _summary_text(summary: dict | str | None) -> str:
    """Readable text for a project_summary_json (prefer narrative/summary keys)."""
    if not summary:
        return ""
    if isinstance(summary, str):
        return summary
    for key in ("narrative", "summary", "overview"):
        val = summary.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return json.dumps(summary, ensure_ascii=False, default=str)


def _recording_text(label: str, date: str | None, mom: dict | None) -> str:
    """Render one recording's MoM into readable text (decisions/việc/blocker/summary)."""
    mom = mom or {}
    lines = [f"Phiên: {label}" + (f" ({date})" if date else "")]
    decisions = _mom_texts(mom.get("decisions"))
    if decisions:
        lines.append("Quyết định: " + "; ".join(decisions))
    for ai in mom.get("action_items") or []:
        if isinstance(ai, dict) and ai.get("item"):
            seg = f"Việc: {ai['item']}"
            if ai.get("pic"):
                seg += f" — {ai['pic']}"
            if ai.get("deadline"):
                seg += f" (hạn {ai['deadline']})"
            lines.append(seg)
    blockers = _mom_texts(mom.get("blockers"))
    if blockers:
        lines.append("Blocker: " + "; ".join(blockers))
    summary = mom.get("summary")
    if isinstance(summary, str) and summary.strip():
        lines.append(summary.strip())
    return "\n".join(lines)


def build_postgres_chunks(
    summary: dict | str | None, recordings: list[dict]
) -> list[dict]:
    """Split the raw Postgres source into embeddable chunks.

    `recordings`: ordered list of {"label", "date", "mom"}. Returns
    [{"label", "text"}] — an optional project-summary chunk first, then one chunk
    per recording. Per-recording chunking lets a per-session prompt ("tóm tắt
    meeting 1") match the ONE relevant session instead of being diluted across the
    whole project.
    """
    chunks: list[dict] = []
    stext = _summary_text(summary)
    if stext:
        chunks.append({"label": "Tổng kết dự án", "text": stext})
    for r in recordings:
        chunks.append({
            "label": r["label"],
            "text": _recording_text(r["label"], r.get("date"), r.get("mom")),
        })
    return chunks


def postgres_whole_text(chunks: list[dict]) -> str:
    """Join chunks into one text (for the mem↔pg fidelity comparison + char size)."""
    return "\n\n".join(c["text"] for c in chunks if c.get("text"))


def best_sim(query_vec: list[float] | None, chunk_vecs: list[list[float] | None]) -> float:
    """Max cosine of the query against any chunk vector (0.0 if none)."""
    sims = [cosine(query_vec, cv) for cv in chunk_vecs if cv]
    return max(sims) if sims else 0.0


def summarize_rows(rows: list[BenchRow]) -> dict:
    """Aggregate the per-prompt rows into an overall view (averages + win counts)."""
    n = len(rows)
    if not n:
        return {}

    def avg(attr: str) -> float:
        return sum(getattr(r, attr) for r in rows) / n

    return {
        "n": n,
        "avg_mem_fetch_ms": avg("mem_fetch_ms"),
        "avg_pg_fetch_ms": avg("pg_fetch_ms"),
        "avg_mem_e2e_ms": avg("mem_e2e_ms"),
        "avg_pg_e2e_ms": avg("pg_e2e_ms"),
        "avg_mem_chars": avg("mem_chars"),
        "avg_pg_chars": avg("pg_chars"),
        "avg_sim_q_mem": avg("sim_q_mem"),
        "avg_sim_q_pg": avg("sim_q_pg"),
        "avg_sim_mem_pg": avg("sim_mem_pg"),
        # how often each source surfaced more prompt-relevant content
        "mem_wins_relevance": sum(1 for r in rows if r.sim_q_mem >= r.sim_q_pg),
        "pg_wins_relevance": sum(1 for r in rows if r.sim_q_pg > r.sim_q_mem),
        # answer-level (0.0 across the board when the LLM didn't run)
        "avg_sim_ans_mem_pg": avg("sim_ans_mem_pg"),
        "avg_sim_ans_mem_gold": avg("sim_ans_mem_gold"),
        "avg_sim_ans_pg_gold": avg("sim_ans_pg_gold"),
    }


def _f(x: float, p: int = 1) -> str:
    return f"{x:.{p}f}"


def render_markdown(rows: list[BenchRow], summary: dict) -> str:
    """Render a human-readable Markdown report: per-prompt table + overall view."""
    lines: list[str] = []
    lines.append("# Agent-memory vs Postgres — retrieval benchmark\n")
    lines.append(
        "So sánh tốc độ truy xuất (fetch & end-to-end có LLM) và độ liên quan ngữ "
        "nghĩa (bge-m3 cosine) giữa **bộ nhớ agent (bản chắt lọc AgentBase)** và "
        "**Postgres (dữ liệu gốc)** cho từng prompt.\n"
    )

    if summary:
        lines.append("## Tổng quan (Overall)\n")
        lines.append(f"- Số prompt: **{summary['n']}**")
        lines.append(
            f"- Fetch trung bình — mem **{_f(summary['avg_mem_fetch_ms'])} ms** vs "
            f"pg **{_f(summary['avg_pg_fetch_ms'])} ms**"
        )
        lines.append(
            f"- End-to-end (có LLM) trung bình — mem **{_f(summary['avg_mem_e2e_ms'])} ms** vs "
            f"pg **{_f(summary['avg_pg_e2e_ms'])} ms**"
        )
        lines.append(
            f"- Kích thước payload trung bình — mem **{_f(summary['avg_mem_chars'], 0)} ký tự** vs "
            f"pg **{_f(summary['avg_pg_chars'], 0)} ký tự**"
        )
        lines.append(
            f"- Độ liên quan (prompt↔nguồn) — mem **{_f(summary['avg_sim_q_mem'], 3)}** vs "
            f"pg **{_f(summary['avg_sim_q_pg'], 3)}**"
        )
        lines.append(
            f"- Độ trung thực bản chắt lọc (mem↔pg) — **{_f(summary['avg_sim_mem_pg'], 3)}**"
        )
        lines.append(
            f"- Thắng độ-liên-quan — mem **{summary['mem_wins_relevance']}** / "
            f"pg **{summary['pg_wins_relevance']}**\n"
        )

    lines.append("## Chi tiết theo prompt\n")
    header = (
        "| Meeting | Prompt | mem fetch | pg fetch | mem e2e | pg e2e | "
        "mem chars | pg chars | sim q↔mem | sim q↔pg | sim mem↔pg |"
    )
    sep = "|" + "---|" * 11
    lines.append(header)
    lines.append(sep)
    for r in rows:
        lines.append(
            f"| {r.meeting} | {r.prompt} | {_f(r.mem_fetch_ms)} | {_f(r.pg_fetch_ms)} | "
            f"{_f(r.mem_e2e_ms)} | {_f(r.pg_e2e_ms)} | {r.mem_chars} | {r.pg_chars} | "
            f"{_f(r.sim_q_mem, 3)} | {_f(r.sim_q_pg, 3)} | {_f(r.sim_mem_pg, 3)} |"
        )
    lines.append("")

    # Answer-quality section — only when the LLM actually produced answers.
    if any(r.mem_answer or r.pg_answer for r in rows):
        lines.append("## Chất lượng câu trả lời (khi chạy LLM)\n")
        lines.append(
            "`đồng thuận` = cosine giữa câu trả lời từ agent-mem và từ Postgres "
            "(cao = bản chắt lọc cho ra cùng câu trả lời như dữ liệu gốc). "
            "`↔gold` chỉ có khi prompt khai báo câu trả lời chuẩn.\n"
        )
        lines.append("| Prompt | đồng thuận (mem↔pg) | mem↔gold | pg↔gold |")
        lines.append("|" + "---|" * 4)
        for r in rows:
            if not (r.mem_answer or r.pg_answer):
                continue
            gold_mem = _f(r.sim_ans_mem_gold, 3) if r.gold else "—"
            gold_pg = _f(r.sim_ans_pg_gold, 3) if r.gold else "—"
            lines.append(
                f"| {r.prompt} | {_f(r.sim_ans_mem_pg, 3)} | {gold_mem} | {gold_pg} |"
            )
        lines.append("")
    return "\n".join(lines)
