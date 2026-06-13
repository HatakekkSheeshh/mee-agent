# create_task reject → terminal (stop the re-attempt loop) — Plan (option 3)

> Execute with `superpowers:executing-plans`, inline. Run with `ECC_GATEGUARD=off`.
> Safety net: `venv/bin/python -m pytest tests/meeting -q` (currently **77 passed**).
> This plan changes ONE test's expectation (see Task 4); net count stays 77.

**Branch:** `feat/backend-agents`.

## Problem (root cause — confirmed from live logs 2026-06-09)

When the user presses **Từ chối** on a `create_task` GATE-1 card, the graph resumes into
`make_agent_execute`, which appends a `{"status":"rejected"}` tool result and returns
`agent_route="agent"` → **loops back to the `agent` node**. Because this is a *resume* (not a
new turn), `agent_messages` is preserved from the checkpoint and still contains the standing
user instruction *"Tạo task cho … trong Meeting 1"*. Real gemma reads that, treats the rejection
as a *failed attempt* (not "user said stop"), and **re-plans**: re-calls `list_recordings` →
`recording_mom` → `create_task` → another GATE-1 interrupt. Bounded only by
`MAX_AGENT_ROUNDS = 6`, so the user gets re-prompted for approval repeatedly.

Live trace that proves it:
```
resume {'action':'rejected'} → agent_approve → agent_execute (create_task rejected)
→ round 2: list_recordings → round 3: recording_mom → round 4: create_task → INTERRUPTED again
```

The unit test `test_agent_side_effect_rejected` hid this: it scripts the FakeLLM to return a
graceful *"OK, mình không gửi nữa."* after reject — i.e. it assumes the model voluntarily stops.
gemma doesn't.

## Verified scoping fact (why this is "option 3" == "option 1")

`agent_approve` and `agent_execute` are reached **only for side-effect tools**. In `agent_tools`,
read tools execute inline and route straight back to `agent` (`agent_route="agent"`, no `pending`);
only a side-effect tool sets `pending` → `agent_route="approve"` → `agent_approve` → `agent_execute`.
So **every** reject handled in `agent_execute` is a side-effect reject — there is no
non-side-effect reject branch to preserve. Making the `agent_execute` reject terminal IS
"treat side-effect rejects as terminal."

## Fix: on reject, finish the turn deterministically (don't loop back to the LLM)

### Task 1 — `agent_execute` reject branch terminal
File: `meeting/graphs/chat_graph/agent.py`, `make_agent_execute`'s else-branch (the
`action != "approved"` path that builds `result = {"status":"rejected", ...}`).
- Keep appending the rejected tool result to `messages` (preserves a valid message list).
- Return `agent_route="finish"` (NOT `"agent"`) **and** a canned `final_reply`, e.g.
  `"Đã hủy — mình không tạo task nữa."` Also clear `pending_tool`/`user_decision` as today.
- The `approved` paths (create_task→reconcile bridge, and normal side-effect execute) are
  **unchanged** — they still return `agent_route="reconcile"`/`"agent"` respectively.

### Task 2 — `route_after_agent_execute` gains a terminal target
File: same. Signature → `Literal["agent", "pm_call", "save_reply"]`.
- `agent_route == "reconcile"` → `"pm_call"` (unchanged).
- `agent_route == "finish"` → `"save_reply"` (NEW).
- else → `"agent"` (unchanged).

### Task 3 — builder edge
File: `meeting/graphs/chat_graph/builder.py`, the `add_conditional_edges("agent_execute",
route_after_agent_execute, {...})` map → add `"save_reply": "save_reply"`.
(Map becomes `{"agent": "agent", "pm_call": "pm_call", "save_reply": "save_reply"}`.)

### Task 4 — update the reject test to the new (terminal) behavior
File: `tests/meeting/test_agent_loop.py::test_agent_side_effect_rejected`.
- The reject no longer triggers a 2nd LLM turn. Remove the scripted `text("OK, mình không gửi nữa.")`
  follow-up (the FakeLLM now only needs the first tool-call response).
- Assert: `ft.calls == []` (never executed — unchanged), the turn is **not** interrupted,
  `final_reply == "Đã hủy — mình không tạo task nữa."` (the canned reply), and `len(llm.calls) == 1`
  (no second LLM round). Mirror this if `test_reconcile_bridge::test_bridge_reject_gate1_no_handoff`
  asserts a model-produced reply — its `final_reply` becomes the canned string and its 2nd
  scripted `_resp_text` is no longer consumed (drop it / adjust the assert).
- Run `pytest tests/meeting -q` → **77 passed**. Commit
  `fix(chat): make create_task reject terminal (stop gemma re-attempt loop)`.

## Self-review / risk
- Deterministic: the fix no longer depends on the model choosing to stop.
- The pm-agent GATE-2 reject path is untouched (that reject goes through `pm_await`/
  `_decision_to_payload`, not `agent_execute`).
- Only behavior change: a rejected side-effect tool ends the turn with a fixed acknowledgment
  instead of giving the agent another round. That's the intended HITL semantics.

## Out of scope (separate findings logged in HANDOFF, do NOT fix here)
- create_task assignee filter matches the MoM **display name** (`pic`, e.g. "Hiệu"), not a
  Redmine **login** ("hieunq3") → login prompts return 0 items.
- create_task aggregates project-level `get_mom_action_items`, ignoring a named recording
  ("trong Meeting 1") — no `recording_id` scoping.
- Summary answers can carry a hallucinated date forward across turns (grounding).
