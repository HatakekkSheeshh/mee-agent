# Unified Q&A + Tool Agent — Implementation Plan (Task #8)

> **For the next session.** Execute with TDD (`tests/meeting/`, `venv/bin/python -m pytest`).
> Spec context: `docs/superpowers/specs/2026-06-06-happy-path-retrieval-reconcile-design.md`.
> Builds on the committed pm-agent branch (Phase 2) — keep `pm_task` working.

## Goal

Replace the brittle `classify_intent` split (`question` vs `tool`) with **one
tool-calling agent** (ReAct loop) that:
- answers normal questions about the bound project, and
- **auto-retrieves from the DB** (no "search database" prompt from the user) and
- calls side-effecting tools (`create_task`, `send_email`) with HITL approval.

`pm_task` stays a **separate** branch (its own A2A + auth + HITL loop) — do NOT
fold it into the same tool loop in this task.

## Current state (what exists today, `meeting/graphs/chat_graph.py`)

- `load_context → classify_intent → {answer | propose_action→execute_action | pm_call…}` → `save_reply`.
- `answer_node` injects only a truncated MoM blurb (project_summary narrative, or
  ≤5 recording MoMs × 200 chars); **no retrieval**, no title resolution.
- Tools (`meeting/services/tools.py`): `search_transcript` (reads DB via
  `repo.join_meeting_transcript`, keyword match), `create_task` (MOCK), `send_email` (MOCK).
- `memory_service` (bge-m3 + tsvector + RRF) exists but is **not wired into chat**.

---

## Pre-flight (DECIDES the architecture — do first)

- [ ] **Probe LLM tool-calling.** Call the configured LLM (`LLM_BASE_URL`/`LLM_MODEL`,
  Qwen3/gpt-oss via MaaS) with `tools=[<dummy>]` + a prompt that should trigger a
  call; check the response has `tool_calls`.
  - **Reliable → Path A:** native OpenAI tool-calling agent.
  - **Unreliable → Path B:** structured-JSON loop — the model emits
    `{"action": "<tool|final>", "args": {...}}`; we parse + dispatch. Same graph
    shape, different node internals. Record the verdict in the agent node docstring.
- [ ] Confirm `memory_service.search()` signature + that `memory_events` has rows for
  test meetings (else retrieval falls back to MoM gracefully — handle empty).

---

## Task 1 — `retrieve` tool (auto-retrieval over the project)

**Files:** `meeting/services/tools.py`; test `tests/meeting/test_tools_retrieve.py`.

- [ ] Add a **read** tool `retrieve` (`side_effect: False`): given `meeting_id` + `query`,
  call `memory_service` hybrid retrieval over the meeting's transcript/`memory_events`
  + MoM; return top-k chunks. Fall back to MoM text if no embeddings.
- [ ] Keep `search_transcript` (or fold into `retrieve`) — decide; don't break callers.
- [ ] Tests: retrieve returns ranked chunks (fake `memory_service`); empty-embeddings
  fallback returns MoM; meeting_id scoping.
- [ ] Commit: `feat(tools): retrieve tool backed by memory_service`

## Task 2 — real `create_task` (reuse `meeting/db`)

**Files:** `meeting/services/tools.py`; (maybe) `meeting/db/repositories.py`.

- [ ] `create_task` stops being a mock: pull MoM `action_items` (repo helper) to
  pre-fill, persist a task row (or return a structured task). `side_effect: True` (HITL).
- [ ] Tests: builds task from MoM action_items; HITL still required.
- [ ] Commit: `feat(tools): create_task reuses MoM/db instead of mock`

## Task 3 — meeting title resolution

**Files:** `meeting/db/repositories.py` (`find_meetings_by_title(user_id, q)` ILIKE);
`chat_graph.py`; test `tests/meeting/test_meeting_resolve.py`.

- [ ] Default = bound `meeting_id`; if the agent/user names a project by title,
  resolve via `find_meetings_by_title` (most-recent on ambiguity, or ask).
- [ ] Tests: bound default; title override; ambiguous → pick most recent.
- [ ] Commit: `feat(chat): resolve meeting by title (bound default)`

## Task 4 — the unified agent node (the core)

**Files:** `meeting/graphs/chat_graph.py` (rewrite the question/tool half); test
`tests/meeting/test_agent_loop.py`. Keep `run_chat_turn`/`resume_chat_turn` signatures.

**Graph:**
```
load_context → agent ⇄ tools → save_reply
                 └ (no tool call) → save_reply
classify_intent removed for question/tool; pm_task still routed separately.
```
- [ ] `agent` node: LLM + tool schemas (Path A native, or Path B JSON). Loops: answer
  directly, or call a tool → `tools` node runs it → result back to `agent`.
- [ ] **HITL**: before executing a `side_effect` tool → `interrupt()` (reuse the
  pending-action machinery in `api/chat.py`); read tools (`retrieve`/`search_transcript`)
  auto-run. Bound the loop (max tool rounds, e.g. 6).
- [ ] Auto-retrieval: the agent calls `retrieve` itself when it needs project content.
- [ ] Keep `pm_task` branch + the `pm_call/pm_await` loop intact.
- [ ] Tests (inject a FakeLLM + fake tools): answer-only; auto-retrieve-then-answer;
  side-effect tool interrupts then resumes; read tool auto-runs; max-rounds cap;
  regression — pm_task still routes.
- [ ] Commit: `feat(chat): unified tool-calling agent (question+tool)`

## Task 5 — HTTP layer + cleanup

**Files:** `meeting/api/chat.py`; `meeting/graphs/__init__.py`.

- [ ] Ensure approve/reject still drive the agent's tool-approval interrupt (the pending
  machinery already exists; the interrupt now originates in the agent loop).
- [ ] Remove dead code (`route_after_classify`, `answer_node`, `propose_action_node`,
  `make_execute_action`) once the agent path is green. Keep exports stable.
- [ ] Full suite green: `venv/bin/python -m pytest tests/meeting -v`.
- [ ] Commit: `refactor(chat): remove classify/answer/propose nodes superseded by agent`

## Deferred / NOT in this task
- **pm_task lifecycle deltas** (parked): resume format branch (`/add`//`cancel` text for
  need_more_info vs DataPart for need_approval — FE cancel already does `/cancel`);
  Edit affordance on need_approval; clear cached `pm_task_id`/`pm_context_id` on terminal;
  bump `PM_MAX_ROUNDS`. Track separately.
- Folding pm_task into the unified tool loop (possible later: expose a `pm_agent` tool).

## Self-review checklist
- Pre-flight verdict (A/B) recorded before building Task 4.
- `pm_task` still works after the refactor (regression test).
- Read vs side-effect tools: only side-effect interrupts.
- `run_chat_turn`/`resume_chat_turn` signatures unchanged.
- Graceful empty-embeddings retrieval fallback.
