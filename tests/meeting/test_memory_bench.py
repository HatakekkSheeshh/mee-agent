"""Pure metric core of the agent-mem-vs-Postgres benchmark (network-free).

The runner (scripts/bench_memory.py) does the timing + network/LLM/embedding I/O;
everything that turns measurements into comparable numbers lives here and is
unit-tested without a DB, an embedding endpoint, or an LLM.
"""
from __future__ import annotations

import math

from meeting.services.memory_bench import (
    BenchRow,
    build_postgres_chunks,
    cosine,
    render_markdown,
    summarize_rows,
)


# ── cosine ───────────────────────────────────────────────────────────────

def test_cosine_identical_vectors_is_one():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_cosine_orthogonal_vectors_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_handles_empty_or_zero_vectors():
    assert cosine([], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_known_value():
    # 45° between (1,0) and (1,1) → cos = 1/sqrt(2)
    assert math.isclose(cosine([1.0, 0.0], [1.0, 1.0]), 1 / math.sqrt(2), rel_tol=1e-9)


# ── build_postgres_chunks ──────────────────────────────────────────────────

def _recordings():
    return [
        {"label": "Meeting 1", "date": "2026-06-01", "mom": {
            "decisions": ["Chốt v1"],
            "action_items": [{"item": "Deploy", "pic": "Hiếu", "deadline": "2026-06-20"}],
            "blockers": ["API upload lỗi"],
        }},
        {"label": "Meeting 4", "date": "2026-06-10", "mom": {
            "action_items": [{"item": "Nghiên cứu SQL Agent", "pic": "anhvd6"}],
        }},
    ]


def test_build_postgres_chunks_one_per_recording_plus_summary():
    chunks = build_postgres_chunks({"narrative": "Đang chạy"}, _recordings())
    # summary chunk first, then one chunk per recording (in given order)
    assert len(chunks) == 3
    assert "Đang chạy" in chunks[0]["text"]
    assert chunks[1]["label"] == "Meeting 1"
    assert chunks[2]["label"] == "Meeting 4"


def test_build_postgres_chunks_renders_mom_content():
    chunks = build_postgres_chunks(None, _recordings())
    # no summary → only per-recording chunks
    assert [c["label"] for c in chunks] == ["Meeting 1", "Meeting 4"]
    m1 = chunks[0]["text"]
    assert "Chốt v1" in m1 and "Deploy" in m1 and "Hiếu" in m1 and "API upload lỗi" in m1
    assert "anhvd6" in chunks[1]["text"]


# ── summarize_rows ─────────────────────────────────────────────────────────

def _row(**kw):
    base = dict(
        meeting="P", prompt="q",
        mem_fetch_ms=10.0, mem_e2e_ms=110.0, pg_fetch_ms=20.0, pg_e2e_ms=420.0,
        mem_chars=500, pg_chars=5000,
        sim_q_mem=0.8, sim_q_pg=0.6, sim_mem_pg=0.7,
    )
    base.update(kw)
    return BenchRow(**base)


def test_summarize_rows_averages_and_relevance_winrate():
    rows = [
        _row(sim_q_mem=0.9, sim_q_pg=0.5),   # mem wins relevance
        _row(sim_q_mem=0.4, sim_q_pg=0.7),   # pg wins relevance
    ]
    s = summarize_rows(rows)
    assert s["n"] == 2
    assert math.isclose(s["avg_mem_fetch_ms"], 10.0)
    assert math.isclose(s["avg_pg_e2e_ms"], 420.0)
    assert s["mem_wins_relevance"] == 1
    assert s["pg_wins_relevance"] == 1


def test_summarize_rows_empty_is_empty_dict():
    assert summarize_rows([]) == {}


# ── render_markdown ────────────────────────────────────────────────────────

def test_render_markdown_has_table_and_summary():
    rows = [_row(prompt="Tóm tắt meeting 1")]
    md = render_markdown(rows, summarize_rows(rows))
    # per-prompt row present
    assert "Tóm tắt meeting 1" in md
    # the four latency dimensions + three similarity dimensions are surfaced
    for col in ("fetch", "e2e", "sim"):
        assert col in md.lower()
    # an overall/summary section exists
    assert "Tổng quan" in md or "Overall" in md or "summary" in md.lower()


# ── answer-level scoring (only when the LLM ran) ───────────────────────────

def test_summarize_includes_answer_agreement_average():
    rows = [
        _row(mem_answer="a", pg_answer="b", sim_ans_mem_pg=0.8),
        _row(mem_answer="a", pg_answer="b", sim_ans_mem_pg=0.6),
    ]
    s = summarize_rows(rows)
    assert math.isclose(s["avg_sim_ans_mem_pg"], 0.7)


def test_render_shows_answer_section_when_answers_present():
    rows = [_row(prompt="Tóm tắt meeting 1", mem_answer="Phiên 1 nói về X",
                 pg_answer="Phiên 1: X", sim_ans_mem_pg=0.91)]
    md = render_markdown(rows, summarize_rows(rows))
    # a dedicated answer-quality section appears, surfacing the agreement metric
    assert "câu trả lời" in md.lower() or "answer" in md.lower()
    assert "0.91" in md


def test_render_omits_answer_section_when_no_answers():
    rows = [_row(prompt="q")]  # mem_answer/pg_answer default ""
    md = render_markdown(rows, summarize_rows(rows))
    assert "Chất lượng câu trả lời" not in md
