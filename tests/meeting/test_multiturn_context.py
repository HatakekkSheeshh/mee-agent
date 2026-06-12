"""Multi-turn context regression (live finding 2026-06-11).

Observed live: turn 1 "email đến andvd6" → agent asks for subject/body;
turn 2 "tiêu đề: Họp chiều nay, nội dung: Họp gấp" → agent asks AGAIN
("Bạn muốn mình làm gì với thông tin này ạ?") instead of merging both turns
into one send_email call.

These tests pin the HARNESS half of that contract:
  1. _seed_agent_messages rebuilds the LLM message list from recent_messages
     (the DB history load_context provides) + the new message, in order;
  2. on the follow-up turn the agent's LLM call actually RECEIVES the turn-1
     exchange (recipient "andvd6" + the agent's own follow-up question);
  3. when the model does the right thing (emits send_email with the merged
     args), the loop interrupts for approval and executes exactly once with
     to/subject/body intact.

If these stay green while the live bug persists, the loss is model/prompt-side
(gemma ignoring provided history), not state plumbing — fix in
_agent_system_prompt, not in the graph.
"""
from __future__ import annotations

import json

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from meeting.graphs._chat_prompts import _agent_system_prompt
from meeting.graphs.chat_graph import _seed_agent_messages
from tests.meeting.test_agent_loop import (
    FakeLLM,
    _build,
    _initial,
    _install,
    _interrupt_value,
    _interrupted,
    text,
    tool,
)

TURN1_USER = "email đến andvd6"
TURN1_AGENT = (
    "Bạn muốn mình gửi email nội dung gì cho anh Duy Anh (andvd6) vậy? "
    "Bạn cho mình biết tiêu đề và nội dung cụ thể nhé!"
)
TURN2_USER = "tiêu đề: Họp chiều nay, nội dung: Họp gấp"

RECENT = [
    {"role": "user", "content": {"text": TURN1_USER}},
    {"role": "agent", "content": {"text": TURN1_AGENT}},
]

MERGED_ARGS = {"to": "andvd6", "subject": "Họp chiều nay", "body": "Họp gấp"}


# ─── 1. seeding is pure plumbing — history precedes the new message ──

def test_seed_includes_prior_turns_in_order():
    msgs = _seed_agent_messages({"recent_messages": RECENT, "user_message": TURN2_USER})
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"] == TURN1_USER
    assert msgs[1]["content"] == TURN1_AGENT
    assert msgs[2]["content"] == TURN2_USER


# ─── 1b. prompt carries an explicit multi-turn MERGE directive ──────
#
# The harness demonstrably hands the model the prior turns (tests 2+3 below);
# the live loss is gemma treating each message as standalone. The only in-process
# lever is the system prompt, so pin that it instructs the model to GỘP a
# follow-up that supplies missing args into the in-flight action instead of
# re-asking. Behavioral proof (does gemma actually merge) is a live run — a
# FakeLLM can't exercise the real model.

def test_system_prompt_has_multiturn_merge_rule():
    prompt = _agent_system_prompt(_initial(TURN2_USER))
    # the continuity rule exists and is named distinctly
    assert "HỘI THOẠI LIÊN TỤC" in prompt
    assert "GỘP" in prompt
    # it explicitly forbids re-asking for already-supplied info
    assert "KHÔNG hỏi lại" in prompt
    # it grounds the rule with the canonical email-across-two-turns example
    assert "send_email" in prompt


# ─── 2+3. follow-up turn: context reaches the LLM; merged call works ─

async def test_followup_turn_sees_history_and_merged_send_email_executes():
    llm = FakeLLM([
        # The CORRECT model behavior for turn 2: merge turn-1 recipient with
        # the just-supplied subject/body into one send_email call.
        tool([{"id": "t1", "name": "send_email", "arguments": json.dumps(MERGED_ARGS)}]),
        text("Đã gửi email cho andvd6."),
    ])
    tools = _install()
    graph = _build(llm, MemorySaver(), tools)
    config = {"configurable": {"thread_id": "multiturn"}}

    # Turn 2 state exactly as the runner builds it: fresh per-turn buffers,
    # history present via recent_messages (what load_context loads from DB).
    state = {**_initial(TURN2_USER), "recent_messages": RECENT}
    await graph.ainvoke(state, config=config)

    # (2) the LLM's first call received the turn-1 exchange — the harness did
    # NOT drop context. If this fails, the bug is in seeding/state plumbing.
    sent = json.dumps(llm.calls[0]["messages"], ensure_ascii=False)
    assert "andvd6" in sent, "turn-1 user message missing from the LLM call"
    assert "tiêu đề và nội dung cụ thể" in sent, "turn-1 agent reply missing"
    assert TURN2_USER in sent

    # (3) side-effect tool → HITL interrupt carrying the MERGED args.
    assert await _interrupted(graph, config)
    pending = await _interrupt_value(graph, config)
    assert pending["tool"] == "send_email"
    for key, val in MERGED_ARGS.items():
        assert pending["args"][key] == val

    # Approve → executes exactly once, args still merged.
    result = await graph.ainvoke(Command(resume={"action": "approved"}), config=config)
    send_calls = [c for c in tools.calls if c["name"] == "send_email"]
    assert len(send_calls) == 1
    for key, val in MERGED_ARGS.items():
        assert send_calls[0]["args"][key] == val
    assert result["final_reply"] == "Đã gửi email cho andvd6."
