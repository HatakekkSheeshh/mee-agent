"""
Task 4 — the pm_call / pm_await / pm_reply loop with HITL.

These tests assemble a minimal LangGraph from the *real* pm nodes (entry at
pm_call, save_reply replaced by a no-op) and drive it with an in-memory
checkpointer + an injected FakeClient. They prove:
  - exactly one A2A send per pm_call invocation (replay-safety),
  - approval / need-more-info round-trips resume correctly,
  - errors and the max-rounds cap end the graph cleanly.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from meeting.graphs.chat_graph import (
    ChatState,
    PM_MAX_ROUNDS,
    make_pm_call,
    pm_await,
    pm_error,
    pm_reply,
    route_after_pm_call,
    route_after_pm_error,
)
from meeting.services.pm_agent_client import PmAgentError, PmAgentResult


# ─── fixtures ────────────────────────────────────────────────────────

def completed(text="Đã liệt kê 3 issue.", task_id="task-1") -> PmAgentResult:
    return PmAgentResult(task_id, "completed", text, False, None, context_id="ctx-1")


def need_approval(issues, task_id="task-1", text="Xác nhận tạo issue?") -> PmAgentResult:
    return PmAgentResult(task_id, "input_required", text, True, issues, context_id="ctx-1")


def need_more_info(task_id="task-1", text="Issue thuộc project nào?") -> PmAgentResult:
    return PmAgentResult(task_id, "input_required", text, False, None, context_id="ctx-1")


class FakeClient:
    """Returns scripted results in order; records every send call."""

    def __init__(self, results, *, repeat_last=False):
        self._results = list(results)
        self.repeat_last = repeat_last
        self.calls: list[dict] = []
        self.cancelled = None

    async def send_message(self, text, *, task_id=None, context_id=None, data_part=None, bearer=None):
        self.calls.append(
            {"text": text, "task_id": task_id, "context_id": context_id, "data_part": data_part, "bearer": bearer}
        )
        idx = len(self.calls) - 1
        if idx < len(self._results):
            return self._results[idx]
        if self.repeat_last and self._results:
            return self._results[-1]
        raise AssertionError("FakeClient: no scripted result for call %d" % idx)

    async def cancel(self, task_id):
        self.cancelled = task_id


class RaisingClient:
    def __init__(self):
        self.calls: list[int] = []

    async def send_message(self, *a, **k):
        self.calls.append(1)
        raise PmAgentError("boom")

    async def cancel(self, task_id):
        pass


# ─── minimal graph from the real pm nodes ────────────────────────────

def _build(pm_client, checkpointer):
    g = StateGraph(ChatState)
    g.add_node("pm_call", make_pm_call(pm_client))
    g.add_node("pm_await", pm_await)
    g.add_node("pm_reply", pm_reply)
    g.add_node("pm_error", pm_error)
    g.add_node("save_reply", lambda state: {})  # stand-in for the DB save node
    g.set_entry_point("pm_call")
    g.add_conditional_edges(
        "pm_call",
        route_after_pm_call,
        {
            "pm_await": "pm_await",
            "pm_reply": "pm_reply",
            "pm_error": "pm_error",
            "save_reply": "save_reply",
        },
    )
    g.add_edge("pm_await", "pm_call")
    g.add_conditional_edges(
        "pm_error",
        route_after_pm_error,
        {"pm_call": "pm_call", "save_reply": "save_reply"},
    )
    g.add_edge("pm_reply", "save_reply")
    g.add_edge("save_reply", END)
    return g.compile(checkpointer=checkpointer)


def _config(thread_id):
    return {"configurable": {"thread_id": thread_id}}


async def _interrupted(graph, config) -> bool:
    snap = await graph.aget_state(config)
    return bool(snap.next)


async def _interrupt_value(graph, config):
    snap = await graph.aget_state(config)
    for task in snap.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


# ─── tests ───────────────────────────────────────────────────────────

async def test_pm_call_completed_reply():
    client = FakeClient([completed("Xong rồi.")])
    graph = _build(client, MemorySaver())
    cfg = _config("t-completed")

    result = await graph.ainvoke({"user_message": "liệt kê issue"}, cfg)

    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Xong rồi."
    assert len(client.calls) == 1
    assert client.calls[0]["text"] == "liệt kê issue"
    assert client.calls[0]["task_id"] is None


async def test_pm_call_need_approval_interrupts():
    issues = [{"actions": "CREATE", "subject": "Deploy v1"}]
    client = FakeClient([need_approval(issues)])
    graph = _build(client, MemorySaver())
    cfg = _config("t-approval")

    await graph.ainvoke({"user_message": "tạo issue deploy v1"}, cfg)

    assert await _interrupted(graph, cfg)
    pending = await _interrupt_value(graph, cfg)
    assert pending["kind"] == "need_approval"
    assert pending["issues"] == issues
    # No second send before the user resumes.
    assert len(client.calls) == 1


async def test_resume_approve_sends_datapart():
    issues = [{"actions": "CREATE", "subject": "Deploy v1"}]
    client = FakeClient([need_approval(issues, task_id="task-42"), completed("Đã tạo issue.")])
    graph = _build(client, MemorySaver())
    cfg = _config("t-resume-approve")

    await graph.ainvoke({"user_message": "tạo issue"}, cfg)
    assert await _interrupted(graph, cfg)

    result = await graph.ainvoke(
        Command(resume={"action": "approved", "approval_action": "approve"}), cfg
    )

    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Đã tạo issue."
    assert len(client.calls) == 2
    second = client.calls[1]
    assert second["task_id"] == "task-42"
    # contextId from the first result must be echoed on resume (avoids the
    # server's -32603 "Context doesn't match TaskManager" error).
    assert second["context_id"] == "ctx-1"
    assert second["data_part"]["approval_action"] == "approve"


async def test_need_more_info_then_approval_then_done():
    issues = [{"actions": "CREATE", "subject": "Deploy v1"}]
    client = FakeClient(
        [need_more_info(task_id="task-9"), need_approval(issues, task_id="task-9"), completed("Đã tạo.")]
    )
    graph = _build(client, MemorySaver())
    cfg = _config("t-multi")

    # Round 1 — start → need_more_info → interrupt
    await graph.ainvoke({"user_message": "tạo issue"}, cfg)
    assert await _interrupted(graph, cfg)
    p1 = await _interrupt_value(graph, cfg)
    assert p1["kind"] == "need_more_info"

    # Resume with free text → need_approval → interrupt
    await graph.ainvoke(Command(resume={"text": "project Mee"}), cfg)
    assert await _interrupted(graph, cfg)
    p2 = await _interrupt_value(graph, cfg)
    assert p2["kind"] == "need_approval"

    # Resume with approval → completed
    result = await graph.ainvoke(
        Command(resume={"approval_action": "approve"}), cfg
    )
    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Đã tạo."

    # Replay-safety: exactly 3 sends across the two interrupts (no double-send).
    assert len(client.calls) == 3
    assert client.calls[0]["text"] == "tạo issue"
    assert client.calls[0]["data_part"] is None
    assert client.calls[1]["text"] == "project Mee"
    assert client.calls[1]["task_id"] == "task-9"
    assert client.calls[2]["data_part"]["approval_action"] == "approve"


async def test_pm_error_interrupts_for_retry_then_cancel():
    client = RaisingClient()  # always raises
    graph = _build(client, MemorySaver())
    cfg = _config("t-error")

    await graph.ainvoke({"user_message": "tạo issue"}, cfg)

    # Transport error now pauses with a retry card instead of ending.
    assert await _interrupted(graph, cfg)
    pending = await _interrupt_value(graph, cfg)
    assert pending["kind"] == "pm_error"
    assert len(client.calls) == 1

    # Cancel → ends, no re-send.
    result = await graph.ainvoke(Command(resume={"approval_action": "reject"}), cfg)
    assert not await _interrupted(graph, cfg)
    assert result.get("final_reply")
    assert len(client.calls) == 1


class RaiseThenOk:
    """Raises a transport error on the first send, returns `result` thereafter."""

    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    async def send_message(self, text, *, task_id=None, context_id=None, data_part=None, bearer=None):
        self.calls.append(
            {"text": text, "task_id": task_id, "context_id": context_id, "data_part": data_part}
        )
        if len(self.calls) == 1:
            raise PmAgentError("Server disconnected without sending a response")
        return self._result

    async def cancel(self, task_id):
        pass


async def test_pm_error_retry_resends_same_payload_and_completes():
    client = RaiseThenOk(completed("Đã đối chiếu."))
    graph = _build(client, MemorySaver())
    cfg = _config("t-retry")

    items = [{"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01"}]
    state = {
        "user_message": "đồng bộ task",
        "pm_next_payload": {
            "kind": "reconcile", "project": "GIP", "items": items,
            "text": "Đối chiếu ... trên dự án GIP:\n1. viết migration",
        },
    }
    await graph.ainvoke(state, cfg)
    assert await _interrupted(graph, cfg)
    assert (await _interrupt_value(graph, cfg))["kind"] == "pm_error"
    assert len(client.calls) == 1

    # Retry (approve) → re-sends the SAME reconcile payload → completes.
    result = await graph.ainvoke(Command(resume={"approval_action": "approve"}), cfg)
    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Đã đối chiếu."
    assert len(client.calls) == 2
    assert "GIP" in client.calls[1]["text"]
    assert client.calls[1]["data_part"]["kind"] == "reconcile_items"
    assert client.calls[1]["data_part"]["items"] == items


async def test_pm_call_reconcile_sends_text_and_datapart():
    client = FakeClient([completed("Đã đối chiếu.")])
    graph = _build(client, MemorySaver())
    cfg = _config("t-reconcile")

    items = [{"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01"}]
    state = {
        "user_message": "đồng bộ task",
        "pm_next_payload": {
            "kind": "reconcile", "project": "GIP", "items": items,
            "text": "Đối chiếu ... trên dự án GIP:\n1. viết migration",
        },
    }
    result = await graph.ainvoke(state, cfg)

    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Đã đối chiếu."
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert "GIP" in sent["text"]
    assert sent["data_part"]["kind"] == "reconcile_items"
    assert sent["data_part"]["project"] == "GIP"
    assert sent["data_part"]["items"] == items


async def test_max_rounds_cap():
    issues = [{"actions": "CREATE", "subject": "X"}]
    client = FakeClient([need_approval(issues)], repeat_last=True)
    graph = _build(client, MemorySaver())
    cfg = _config("t-maxrounds")

    await graph.ainvoke({"user_message": "tạo issue"}, cfg)
    # Keep approving until the graph stops interrupting (hits the cap).
    guard = 0
    while await _interrupted(graph, cfg):
        guard += 1
        assert guard <= PM_MAX_ROUNDS + 2, "loop did not terminate at the cap"
        await graph.ainvoke(Command(resume={"approval_action": "approve"}), cfg)

    # The cap stops further sends: PM_MAX_ROUNDS sends, then an abort with no send.
    assert len(client.calls) == PM_MAX_ROUNDS
    final = await graph.aget_state(cfg)
    assert final.values.get("final_reply")
