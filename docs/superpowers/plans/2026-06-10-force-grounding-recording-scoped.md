# Force grounding for recording-scoped questions — Implementation Plan

> Execute with `superpowers:executing-plans`, inline, TDD. Run with `ECC_GATEGUARD=off`.
> Safety net: `venv/bin/python -m pytest tests/meeting -q` must stay green (currently 82).
> **Branch:** `feat/backend-agents`.

## Problem (root cause, confirmed via live logs)

For "tóm tắt phiên/Meeting N" type questions, the agent sometimes emits a final answer with **zero
tool calls** — it regurgitates a stale prior summary sitting in `recent_messages` (seeded into the
prompt by `_seed_agent_messages`) instead of calling `list_recordings`/`recording_mom` to read the
real `mom_json`. Result: wrong date / wrong meeting's content. The enabling line is the escape hatch
in `_agent_system_prompt` (`meeting/graphs/_chat_prompts.py`):

> "Với câu hỏi THÔNG TIN (hỏi-đáp), khi đã đủ dữ liệu thì trả lời trực tiếp (KHÔNG gọi tool)."

clear-chat-session (already shipped) only *mitigates* by emptying history. This plan is the **deeper
fix**: force at least one grounding tool call on the first agent turn for recording-scoped /
information questions, so the model cannot answer from stale context alone.

## Approach

Mechanical, not just prompt: on the **first** agent round (`agent_rounds == 0`) for an
information-seeking turn, call the LLM with `tool_choice="required"` instead of `"auto"`, so gemma
*must* emit a tool call before it can produce a final answer. Subsequent rounds stay `"auto"` (so it
can answer once grounded → loop still terminates). Gate it so it does NOT fire for the pm branch or
when a side-effect (create_task/send_email) is already the obvious intent — those already mandate a
tool via the prompt and shouldn't be forced into a *read* tool.

Signal source: extend `classify_intent` to return, alongside `intent`, a `grounding` field
(`"required" | "auto"`) — `"required"` for content/recording questions, `"auto"` otherwise. Thread
it through `ChatState.grounding`. Keep it cheap: it's the same LLM call that already classifies.

⚠️ **Unknown to de-risk first:** does the MaaS gemma endpoint honor `tool_choice="required"`?
Task #8's probe only tested `"auto"`. Task 0 below probes it; if unsupported, fall back to forcing a
specific function (`tool_choice={"type":"function","function":{"name":"list_recordings"}}`) or, last
resort, prompt-only hardening (Task 4) and skip the mechanical force.

## Task 0 — Probe `tool_choice="required"` (no production change)

- Extend `scripts/probe_tool_calling.py` (or a throwaway) to send one chat with
  `tool_choice="required"` and one with a forced function, against the configured LLM. Record
  whether gemma returns a `tool_calls` message (not a content answer).
- **Decision gate:** if `"required"` works → proceed with Tasks 1–3 as written. If only forced-
  function works → use that form in Task 3. If neither works → skip Tasks 1–3, do **Task 4 only**
  (prompt hardening) and note the limitation in the handoff.
- No commit (probe only); record the verdict in the Task 3 code comment.

## Task 1 — `classify_intent` emits a `grounding` flag (TDD)

File: `meeting/graphs/chat_graph/classify.py` + `meeting/graphs/_chat_prompts.py` (CLASSIFY prompt)
+ `meeting/graphs/_chat_state.py` (`ChatState`).
- Test first (`tests/meeting/test_chat_routing.py` or a new `test_grounding_flag.py`, FakeLLM):
  a recording/content question ("tóm tắt phiên 1", "Hiếu cần làm gì trong Meeting 2") →
  classify result carries `grounding == "required"`; a chit-chat / pm question → `"auto"`.
- Implement: add `grounding: NotRequired[Literal["required","auto"]]` to `ChatState`. Extend the
  classify system prompt to also return it in the JSON (`{"intent":…, "grounding":…}`), and have
  `make_classify_intent` parse + default it to `"auto"` when absent (robust to model omitting it).
- Keep the existing `intent` contract unchanged. Green. Commit
  `feat(chat): classify emits grounding flag for content/recording questions`.

## Task 2 — thread `grounding` into agent state (TDD)

File: `meeting/graphs/chat_graph/classify.py` (return value) + wherever classify's dict is merged.
- Test: after `classify_intent`, `state["grounding"]` is set; it survives into the `agent` node.
- Implement: include `grounding` in the classify node's returned dict. Green. (May fold into Task 1's
  commit if trivial.)

## Task 3 — `agent` forces a tool on round 0 when grounding required (TDD)

File: `meeting/graphs/chat_graph/agent.py` (`make_agent` → `agent`).
- Test first (`tests/meeting/test_agent_loop.py`, FakeLLM records `create`/`kwargs`):
  - grounding `"required"` + `agent_rounds == 0` → the LLM call uses `tool_choice="required"`
    (assert on `llm.calls[0]["tool_choice"]`).
  - round ≥ 1 → back to `"auto"` (so the post-tool answer turn can finish → loop terminates).
  - grounding `"auto"` → `"auto"` on every round (current behavior; existing tests stay green).
  - Add a flow test: scripted FakeLLM (round0 forced → `recording_mom` → answer) for a recording
    question asserts at least one tool ran before the final reply.
- Implement: compute `tc = "required" if (state.get("grounding") == "required" and rounds == 0) else
  "auto"`; pass `tool_choice=tc`. Use the form Task 0 verified. Keep `max_tokens`/timeout. Comment
  the round-0-only rationale + the Task-0 verdict.
- Green (full suite). Commit `fix(chat): force a grounding tool call on first agent turn for
  recording-scoped questions`.

## Task 4 — prompt hardening (always; the fallback if Task 0 fails)

File: `meeting/graphs/_chat_prompts.py` (`_agent_system_prompt`).
- Narrow the escape hatch so it cannot apply to recording-scoped questions: change the last rule to
  explicitly exclude "tóm tắt một phiên / Meeting N / hỏi nội dung một recording" — for those, MUST
  call `list_recordings`/`recording_mom` first even if context seems sufficient. Mirror the existing
  create_task carve-out wording.
- No new behavior test required beyond Task 3's flow test, but assert the prompt contains the new
  carve-out string (cheap regression, like `test_classify_prompt_routes_*`).
- Green. Commit `fix(chat): forbid answer-direct for recording-scoped questions in agent prompt`.

## Out of scope (separate follow-ups, see HANDOFF)

- create_task login↔display-name assignee filter + recording scoping.
- Reconcile per-assignee chunking.
- Stripping/limiting stale assistant summaries in `load_context`/`_seed_agent_messages` (an
  alternative mitigation; tool-choice forcing is the chosen primary fix).

## Self-review / risk

- No schema change, no migration.
- Forcing `tool_choice` only on round 0 preserves loop termination (round ≥1 is `"auto"`).
- If gemma ignores `"required"` (Task 0), the mechanical force is dropped and only the prompt
  hardening (Task 4) ships — note the residual risk in the handoff, since prompt-only is what failed
  before. Consider, as a stronger fallback, auto-running `list_recordings` in a pre-agent node for
  grounding-required turns (design first; not in this plan).
- Still unit-only until the live blockers (psycopg + DB revision) are cleared; add a live smoke of
  "tóm tắt Meeting N" to the verification once the backend runs.
