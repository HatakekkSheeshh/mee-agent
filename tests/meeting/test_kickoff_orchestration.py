"""Kickoff orchestration — fetch role data + generate the greeting.

All seams are injected (Redmine MCP `call_tool`, the LLM `generate`), so the
whole flow is unit-tested offline. Mirrors the suite convention of testing the
delegated logic rather than a live-DB endpoint (see test_chat_api_pm.py).
"""
from __future__ import annotations

from src.services.kickoff import (
    DEFAULT_KICKOFF,
    fetch_role_data,
    run_kickoff,
    summarize_redmine_results,
)


# ─── summarize_redmine_results (pure) ─────────────────────────────────

def test_summarize_counts_items_per_tool():
    results = {
        "get_workload_by_assignee": {"issues": [{"id": 1}, {"id": 2}]},
        "get_unassigned_issues": {"issues": [{"id": 9}]},
    }
    out = summarize_redmine_results(results)
    assert "2" in out  # own workload count
    assert "1" in out  # unassigned count


def test_summarize_skips_errors_and_uncountable():
    results = {
        "get_workload_by_assignee": {"error": "redmine mcp error: boom"},
        "list_redmine_issue": {"unexpected": "shape"},
    }
    assert summarize_redmine_results(results) == ""


# ─── fetch_role_data (async, injected call_tool) ──────────────────────

async def test_fetch_role_data_runs_tools_and_summarizes():
    async def call_tool(name):
        return {"issues": [{"id": 1}, {"id": 2}, {"id": 3}]}

    out = await fetch_role_data(["get_workload_by_assignee"], call_tool=call_tool)
    assert "3" in out


async def test_fetch_role_data_skips_failing_tool():
    async def call_tool(name):
        raise RuntimeError("mcp down")

    out = await fetch_role_data(["list_redmine_issue"], call_tool=call_tool)
    assert out == ""


async def test_fetch_role_data_empty_tools_returns_empty():
    async def call_tool(name):  # pragma: no cover - must not be called
        raise AssertionError("should not call any tool")

    assert await fetch_role_data([], call_tool=call_tool) == ""


# ─── run_kickoff (orchestrator) ───────────────────────────────────────

class _Role:
    def __init__(self, name, description, data_plan, kickoff_prompt):
        self.name = name
        self.description = description
        self.data_plan = data_plan
        self.kickoff_prompt = kickoff_prompt


async def test_run_kickoff_own_tasks_grounds_and_returns_greeting():
    role = _Role("AI Applied", "Ứng dụng AI.", "own_tasks", "Tập trung việc riêng.")
    captured = {}

    async def call_tool(name):
        return {"issues": [{"id": 1}, {"id": 2}]}

    def generate(messages):
        captured["content"] = messages[0]["content"]
        return "Chào Anh, hôm nay bạn có 2 task."

    out = await run_kickoff(role=role, user_name="Anh", call_tool=call_tool, generate=generate)
    assert out == "Chào Anh, hôm nay bạn có 2 task."
    assert "2" in captured["content"]  # fetched count reached the prompt


async def test_run_kickoff_no_role_does_not_fetch_data():
    async def call_tool(name):  # pragma: no cover
        raise AssertionError("no role → must not fetch data")

    def generate(messages):
        return "Chào bạn, mình là Mee."

    out = await run_kickoff(role=None, user_name="Anh", call_tool=call_tool, generate=generate)
    assert out == "Chào bạn, mình là Mee."


async def test_run_kickoff_llm_failure_returns_fallback():
    role = _Role("BA", "Phân tích.", "cross_project", "Tổng quan.")

    async def call_tool(name):
        return {"issues": []}

    def generate(messages):
        raise RuntimeError("llm 500")

    out = await run_kickoff(role=role, user_name="Anh", call_tool=call_tool, generate=generate)
    assert out == DEFAULT_KICKOFF


async def test_run_kickoff_strips_think_tags():
    role = _Role("AI Applied", "AI.", "minimal", "Chào.")

    async def call_tool(name):  # pragma: no cover
        raise AssertionError("minimal → no fetch")

    def generate(messages):
        return "<think>nội bộ</think>Chào bạn, mình là Mee."

    out = await run_kickoff(role=role, user_name="Anh", call_tool=call_tool, generate=generate)
    assert out == "Chào bạn, mình là Mee."
