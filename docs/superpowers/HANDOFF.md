# Session Handoff — Mee meeting-agent (feat/backend-agents)

**Branch:** `feat/backend-agents`  ·  **Last updated:** 2026-06-09

Read this first when resuming. It captures state a fresh session can't infer from git alone.

## Kickoff message to paste into the new session

> Continue Mee on branch `feat/backend-agents`. Read CLAUDE.md, `docs/superpowers/HANDOFF.md`,
> and `docs/superpowers/plans/2026-06-09-clear-chat-session.md` (the current plan).
> The chat_graph reorg is fully DONE (Phase 1 + 2: `_chat_*.py` helpers, DI seams, split into the
> `meeting/graphs/chat_graph/` package with a facade) — committed, **77 tests green**
> (`venv/bin/python -m pytest tests/meeting -q`).
> NEXT = execute the **clear-chat-session** plan with `superpowers:executing-plans`, inline, TDD:
> a `POST /api/chat/sessions/{id}/clear` endpoint + `repo.clear_chat_session` that deletes the
> session's chat_messages + pending_actions IN PLACE (keep session_id/meeting_id) and best-effort
> purges the LangGraph checkpoint thread (`get_checkpointer().adelete_thread(str(id))`), plus a
> "Xóa hội thoại" button in ChatPane (confirm → empty thread + re-show welcome, clear localStorage
> messages but keep the session id). Spec:
> `docs/superpowers/specs/2026-06-09-clear-chat-session-design.md`. 3 tasks, adds tests, suite
> stays green. Run with `ECC_GATEGUARD=off`.
> ALSO PENDING (separate plans, not started): `2026-06-09-create-task-reject-terminal.md`
> (option 3 — make create_task reject terminal). Do NOT fix the other create_task findings
> (login↔name filter, recording scoping) — logged below as follow-ups.

## DONE this session (2026-06-09) — bridge live, retry, card, tools package

All committed on `feat/backend-agents` (unpushed); suite **77 passed**
(`venv/bin/python -m pytest tests/meeting -q`).

1. **create_task → pm reconcile bridge** (the 8-task plan below) — DONE & committed earlier
   this branch. GATE 1 (local template) → bridge `kind="reconcile"` → GATE 2 (pm write approval).
   Verified working live ("temporarily it successful").
2. **`_build_reconcile_template` assignee filter** — "tạo task cho `<người>`" narrows MoM items
   to that person (matches `assignee`/`pic`, case-insensitive); shape stays `{project, items}`.
3. **Routing prompts** — `classify_intent` biases "tạo task template / đồng bộ lên Redmine" to the
   **agent** branch (not pm_task); `_agent_system_prompt` now MANDATES calling `create_task`
   (don't answer in text) and says: for meeting-derived tasks pass `assignee`, NOT `title`.
4. **pm-agent transport-error RETRY** — new `pm_error` node: on `PmAgentError`, `pm_call` routes
   to `pm_error` (interrupt, no send) instead of ending; resume "approve" re-sends the **preserved**
   `pm_next_payload`, "reject" cancels. `route_after_pm_error`; `ChatState.pm_last_error`;
   `pm_error` excluded from `_initial_turn_state` reset path. FE: `pm_error` card (Retry/Hủy).
5. **Editable grouped create_task card** (`CreateTaskCard.tsx`) — parses `{project, items}`,
   renders **project + groups-by-assignee**, each item editable (subject/due/description) with
   **persistent labels** (was placeholder-only → looked unlabeled once filled), remove-item,
   card-level `reason` note. Flattens back to `{project, items}` as `edited_args` on approve.
   ⚠️ Known: `reason` is persisted on the pending-action row but **not** consumed downstream
   (audit-only for approve); item `description` DOES flow to Redmine via reconcile items.
6. **`meeting/services/tools/` package** — split `tools.py` into one module per tool + a local
   **`@tool`** decorator (`_registry.py`) that auto-registers into `TOOLS`. Same contract
   (`side_effect`, injected `session`/`user_id`, JSON-schema, `execute_tool` audit). Did NOT use
   `langchain_core.@tool` (StructuredTool lacks side_effect / per-call DI / our schema). `__init__`
   re-exports `repo`/`get_memory_service`/`build_task_items`; `retrieve` resolves
   `get_memory_service` via the package at call time so the test monkeypatch works.

### Open follow-ups noted (not done)
- **Reconcile chunking** — a 23-item reconcile timed out: the agentbase-runtime **gateway**
  disconnected mid-LLM-reconcile (`RemoteProtocolError`, NOT auth, NOT our 60s client timeout).
  Fix = send one `message/send` **per assignee group** (chunk) so each call finishes under the
  gateway timeout. Retry card helps transient blips but re-sending the same 23-item payload will
  re-time-out. PARKED.
- **`reason` field** on the create_task card is audit-only — consider removing it or wiring it
  (append to `_reconcile_text` / item descriptions).
- `.claude/settings.local.json` has a local GateGuard-off env (gitignored, not committed).

## DONE this session (2026-06-09b) — chat_graph reorg (Phase 1 + 2) + 3 live findings

All committed on `feat/backend-agents` (unpushed); suite **77 passed**.

- **Phase 1** (`docs/superpowers/plans/2026-06-09-chat-graph-reorg.md`) — extracted the
  seam-free helpers out of `chat_graph.py`: `_chat_state.py` (ChatState + PM_MAX_ROUNDS +
  MAX_AGENT_ROUNDS), `_chat_llm.py` (`_llm_client`/`_llm_model`), `_chat_prompts.py`
  (`CLASSIFY_SYSTEM_PROMPT` + `_agent_system_prompt` + `_to_llm_messages`), `_chat_serde.py`
  (8 pure serde/payload helpers). Pure motion + re-imports. (One forced test retarget:
  `test_classify_prompt_routes_meeting_tasks_to_agent` now inspects `CLASSIFY_SYSTEM_PROMPT`.)
- **Phase 2** (`docs/superpowers/plans/2026-06-09-chat-graph-phase2-di-split.md`, approach B):
  - **DI seams** — agent factories (`make_agent`/`make_agent_tools`/`make_agent_execute`) take a
    keyword `tools` bundle (default `meeting.services`); `agent_approve` → `make_agent_approve`
    factory; `classify_intent` → `make_classify_intent(llm)`. Tests inject a `FakeToolset` /
    fake LLM instead of `monkeypatch.setattr(chat_graph, list_tools/get_tool/execute_tool/_llm_client)`.
  - **`repo` deliberately NOT injected** — `chat_graph.repo.*` is a module-attribute patch
    (shared state), which survives the package split; tests still patch it that way.
  - **Package split** — `chat_graph.py` → `meeting/graphs/chat_graph/` package: `context.py`,
    `classify.py`, `agent.py`, `pm.py`, `builder.py`, `runner.py`, and `__init__.py` is a
    **facade** re-exporting every public name (incl. `repo`). `from meeting.graphs.chat_graph
    import X` and `chat_graph.X` still resolve. The Phase-1 `_chat_*.py` files stay as
    `graphs/` siblings. `docs/diagrams/chat_graph.mmd` refreshed (verified == live draw_mermaid).

### 3 live findings (verified via run_meeting.py logs + biên bản) — NOT yet fixed
1. **create_task reject re-attempt loop** — pressing Từ chối loops back to `agent`; gemma
   re-runs `list_recordings → recording_mom → create_task` because the standing "tạo task"
   instruction persists in `agent_messages`. **→ NEXT plan fixes this (option 3, terminal reject).**
2. **create_task assignee filter is display-name only** — items carry `pic` = "Hiệu"; prompting
   "tạo task cho **hieunq3**" (a Redmine login) matches nothing (`"hieunq3"` not a substring of
   "hiệu"). Also create_task aggregates project-level `get_mom_action_items` and ignores a named
   recording ("trong Meeting 1") — no `recording_id` scoping. Builds tasks from **action_items
   only** (not decisions/commitments/blockers). FOLLOW-UP, not in the next plan.
2b. The agent DOES call `list_recordings`/`recording_mom` in the create_task flow (proven by logs).
3. **Summary regurgitates stale history instead of grounding — ROOT CAUSE CONFIRMED.**
   "tóm tắt phiên họp meeting 1" answered with the WRONG date (04/06 vs real 03/06) and content
   from a DIFFERENT meeting (Meeting Note / "Hiệu" / speaker diarization — none of which is in
   record 1's `mom_json`, whose `date` is correctly `2026-06-03`). Decisive log:
   `load_context (recent_msgs=10) → classify_intent → [Node agent] final answer` with **NO
   `tool_calls` line** — the agent called ZERO tools, so it never read the recording. It
   regurgitated a prior (wrong) summary sitting in `recent_messages` (loaded by `load_context`,
   seeded by `_seed_agent_messages`), enabled by the `_agent_system_prompt` escape hatch *"khi đã
   đủ dữ liệu thì trả lời trực tiếp (KHÔNG gọi tool)"*. A fresh session grounds correctly (empty
   history). **MITIGATION = the clear-chat-session feature (NEXT plan).** DEEPER FIX (separate
   follow-up): force a grounding call for recording-scoped / "tóm tắt phiên/Meeting N" questions
   — tighten the prompt or set `tool_choice` so the answer-directly hatch can't skip
   `list_recordings`/`recording_mom`.

## NEXT — clear chat session (in-place)

**Plan:** `docs/superpowers/plans/2026-06-09-clear-chat-session.md` ·
**Spec:** `docs/superpowers/specs/2026-06-09-clear-chat-session-design.md`. User-facing mitigation
for finding #3: a "Xóa hội thoại" button + `POST /sessions/{id}/clear` that deletes the session's
chat_messages + pending_actions in place (keep session_id/meeting_id) and purges the LangGraph
checkpoint thread (`adelete_thread`, best-effort). 3 TDD tasks, adds tests, suite stays green.

## ALSO PENDING — create_task reject-terminal (option 3)

**Plan:** `docs/superpowers/plans/2026-06-09-create-task-reject-terminal.md`. Make the
`agent_execute` reject branch terminal (route to `save_reply` with a canned reply) instead of
looping back to the LLM. 3 prod edits (`agent.py` reject branch + `route_after_agent_execute` +
`builder.py` edge) + update `test_agent_side_effect_rejected` (and the reject assert in
`test_reconcile_bridge`) to the no-2nd-LLM-turn behavior. Suite stays 77. **Verified scoping
fact:** `agent_approve`/`agent_execute` are reached ONLY for side-effect tools, so this IS
"side-effect rejects terminal" (option 3 == option 1 in practice).

## Reference artifacts (all committed, self-contained)

- `docs/superpowers/plans/2026-06-08-unified-qa-tool-agent.md` — **current plan (Task #8)**.
- `docs/superpowers/specs/2026-06-06-happy-path-retrieval-reconcile-design.md` — title-scoped
  retrieval + create_task→reconcile design.
- `docs/pm-agent-graph.md` — pm-agent's full LangGraph (auth, classify, per-skill nodes,
  need_more_info pause, issue_approve, reconcile).
- `docs/superpowers/specs/2026-06-02-pm-agent-a2a-chat-design.md` + `plans/2026-06-02-…` — Phase 2.
- `CLAUDE.md` — repo architecture + critical gotchas.

## pm-agent integration — verified facts (live)

- Auth = **per-user Microsoft OIDC**, sent as `Authorization: Bearer <token>` (NOT static
  X-API-KEY). Code reads env **`PM_AGENT_URL`** + **`TOKEN_AUTHEN_PM_AGENT`**; URL must end `/a2a/`.
- Resume MUST echo **both `taskId` and `contextId`** (else `-32603` "Context doesn't match
  TaskManager"). Captured in `PmAgentResult.context_id` + `ChatState.pm_context_id`.
- pm-agent surfaces auth via a `need_more_info` message with a `/auth?url=…` link; ends a
  need_more_info thread on the text **`/cancel`** (the FE "Hủy" now sends that).
- Client sends Bearer + X-API-KEY (works against deployed endpoint or a local pm-agent).

## DONE this session

1. **`CLAUDE.md`** authored (repo guide).
2. **Design + lean plan** for pm-agent A2A integration written & committed.
3. **Phase 1 — ChatPane is now interactive** (frontend only, typechecks clean via `tsc --noEmit`):
   - `meeting_frontend_react/src/types/api.ts` — added `PendingAction`, `ChatTurnResult`.
   - `meeting_frontend_react/src/api/client.ts` — fixed `chat.send` to post `{text}` (was wrong
     `{message}`); replaced broken `resume`→`/resume` with `approve`/`reject` →
     `/api/chat/pending-actions/{id}/approve|reject`.
   - `meeting_frontend_react/src/i18n.ts` — added `chat.thinking/error/approve/reject/pending` (VI+EN).
   - `meeting_frontend_react/src/components/ChatPane.tsx` — was a static mockup; now stateful
     (controlled input, Enter-to-send, lazy session create per meeting, message thread, HITL
     approve/reject card, busy/error states).
4. **README** — clarified Python install (incl. `psycopg[binary]`) + UI/npm install; fixed `.venv`→`venv`.

5. **Phase 2 — pm-agent A2A chat branch (backend, Tasks 1–6 of the plan)** — DONE, 26 tests pass
   (`venv/bin/python -m pytest tests/meeting -v`):
   - `meeting/services/pm_agent_client.py` — thin httpx A2A v0.3 JSON-RPC client
     (`PmAgentClient.send_message/cancel`, `PmAgentResult`, `PmAgentError`); exported from
     `meeting/services/__init__.py`.
   - **Open Q #3 RESOLVED:** non-streaming `message/send` *does* return the interrupted Task
     (state `input-required` + `approval_request` DataPart) in the response body — verified by
     reading the a2a-sdk's `DefaultRequestHandler.on_message_send` /
     `ResultAggregator.consume_and_break_on_interrupt` (only `auth-required` breaks early;
     `input-required` lets the queue drain and returns the aggregated Task). No SSE needed.
   - `meeting/graphs/chat_graph.py` — `pm_task` intent + `pm_call` (one idempotent send, no
     interrupt) / `pm_await` (the only `interrupt()`) / `pm_reply`, looped, capped by
     `PM_MAX_ROUNDS=6`. `build_chat_graph(…, pm_client=None)` seam (prod lazily resolves
     `get_pm_agent_client()` inside `pm_call`, so non-PM chats need no `PM_AGENT_*`).
     `resume_chat_turn` now detects re-interrupts (need_more_info → need_approval).
   - `meeting/api/chat.py` — `ApprovalRequest` gains `approval_action` + `text`; pm interrupts
     persist as `PendingAction(tool_name="pm_agent")`; approve/reject build the pm decision.
     Logic in pure helpers (`_persist_fields`/`_approve_decision`/`_reject_decision`).
   - `.env.example` — `PM_AGENT_A2A_URL` / `PM_AGENT_API_KEY` / `PM_AGENT_TIMEOUT`.
   - Test infra: first suite for `meeting/` under `tests/meeting/` (`pytest.ini` asyncio
     auto-mode scoped there; `conftest.py` seeds dummy env); `requirements-dev.txt`.

## DONE since 2026-06-06 (this session)

- **pm-agent auth verified LIVE** (Bearer + Microsoft token → 200, real Redmine data).
- **`-32603` contextId bug fixed** — echo taskId + contextId on resume.
- **Client**: read `PM_AGENT_URL`/`TOKEN_AUTHEN_PM_AGENT`, Bearer auth, trailing-slash
  normalization, strip pm-agent's `/add … /cancel` hint line for display.
- **Chat UX (FE)**: markdown rendering; pending cards parsed by kind (need_more_info =
  reply input + Gửi/Hủy, need_approval = issues + approve/reject); welcome banner;
  localStorage-persisted thread per meeting (survives F5); need_more_info "Hủy" → `/cancel`.
- **Task #8 plan** written + committed.

## Task #8 — DONE (2026-06-08, this session) — unified tool-calling agent

Implemented per `plans/2026-06-08-unified-qa-tool-agent.md`, full TDD, **48 tests green**
(`venv/bin/python -m pytest tests/meeting -v`). 5 commits on `feat/backend-agents`
(`5361a76`→`1d26611`), unpushed.

- **Pre-flight verdict = Path A (native tool-calling).** `scripts/probe_tool_calling.py`
  proved the MaaS endpoint (actually **`google/gemma-4-31b-it`**, NOT Qwen3 as CLAUDE.md
  says) returns reliable `tool_calls` + parseable args, and answers directly when no tool
  is needed (loop terminates). Verdict recorded in `chat_graph.py` agent-section comment.
- **Task 1** `retrieve` read tool (`tools.py`) — hybrid retrieval via `memory_service`,
  MoM-text fallback on empty embeddings. Threaded optional `meeting_id` include-filter
  through `memory_service.retrieve` + `repo.retrieve_memory_events`.
- **Task 2** `create_task` no longer mock — builds structured tasks from explicit args OR
  the meeting's MoM `action_items` (new `repo.get_mom_action_items`). Still `side_effect`.
- **Task 3** `repo.find_meetings_by_title` (ILIKE, user-scoped) + `chat_graph.resolve_meeting`
  (bound default / title override / most-recent on ambiguity).
- **Task 4** unified agent: `load_context → classify_intent (binary: pm_task|agent) →
  agent ⇄ agent_tools → (agent_approve interrupt → agent_execute) ↺ → save_reply`.
  Replay-safe (LLM/exec nodes never interrupt; only `agent_approve` does, no side effects
  → side-effect tools run exactly once). Read tools auto-run; `meeting_id` injected
  server-side (stripped from LLM schema). New `switch_meeting` tool re-scopes by title.
  `MAX_AGENT_ROUNDS=6`. `pm_task` branch untouched (regression tests pass).
- **Task 5** removed dead `answer_node`/`propose_action_node`/`make_execute_action`/
  `route_after_classify` + `proposed_*` state. **`api/chat.py` unchanged** — the agent's
  approve interrupt reuses the local-tool payload shape `{tool,args,rationale,description}`,
  so existing approve/reject machinery drives it.

## Option B — DONE (2026-06-08, this session) — recording-scoped task queries

Implemented per the spec below, full TDD, **13 new tests** (suite now **64 green**:
`venv/bin/python -m pytest tests/meeting -q`). Committed `591a6dd` (unpushed).

- **repo** (`meeting/db/repositories.py`): `list_recordings` (→ `[{recording_id,
  label, date, has_mom}]`, chronological; label = `title or session_label`; date =
  event date w/ `started_at` fallback) + `get_recording_mom` (recording's `mom_json`).
- **tools** (`meeting/services/tools.py`, both read-only): `list_recordings`
  (`meeting_id` auto-injected) + `recording_mom` (arg `recording_id`) — agent maps
  "Meeting N"/ordinal/date → recording, reads that recording's structured MoM, filters
  `action_items` by `pic`.
- **agent** (`chat_graph.py`): `_agent_system_prompt` only — steers recording-scoped
  lookup + forbids cross-recording mis-attribution (Option C mitigation). **No graph
  change** (read tools auto-run in `agent_tools`; verified by `test_agent_recording_scope`).
- Tests: `test_repo_recordings.py`, `test_tools_recording_scoped.py`,
  `test_agent_recording_scope.py`.

### Original design notes (Option B)

**Problem (verified against live data):** `retrieve` is project-scoped — `memory_events`
carry only `meeting_id`, NOT `recording_id` — so "Hiệu's tasks in *Meeting 1*" returns
the whole project's aggregated memory and the LLM mis-attributes it to the named
recording. The agent has no concept of recordings (only `switch_meeting` by project
title). Accurate per-recording data already exists in `recordings.mom_json.action_items`
(correct `pic`); it just isn't queried at recording granularity.

**Chosen fix = Option B (no schema change, no migration):** add per-recording MoM tooling.
TDD in `tests/meeting/`. Steps:
1. **repo** (`meeting/db/repositories.py`):
   - `list_recordings(session, meeting_id) -> [{recording_id, label, date, has_mom}]`
     (label = `title or session_label`; reuse `get_meeting`).
   - `get_recording_action_items(session, recording_id) -> list[dict]` and/or
     `get_recording_mom(session, recording_id) -> dict|None`.
2. **tools** (`meeting/services/tools.py`, both `side_effect: False`):
   - `list_recordings` — `meeting_id` auto-injected (already stripped from LLM schema);
     lets the agent map "Meeting 1"/ordinal/date → `recording_id`.
   - `recording_mom` — arg `recording_id`; returns that recording's structured MoM
     (summary/decisions/action_items w/ `pic`). For "X's tasks in recording Y" the agent
     reads action_items and filters by `pic`. Keep `retrieve` for cross-recording semantic Q&A.
3. **agent wiring** (`chat_graph.py`): NO graph change (read tools auto-run in `agent_tools`).
   Update `_agent_system_prompt`: for a specific recording/phiên/"Meeting N", call
   `list_recordings` then `recording_mom`; CITE which recording each fact came from;
   NEVER attribute a fact to a recording it didn't read (this is also Option C mitigation).
4. **tests:** tool tests (fake repo) for both tools + read-only flag; agent_loop test —
   scripted FakeLLM does `list_recordings → recording_mom → answer` scoped to one recording.

Note: a recording named "Meeting 1" lives in project **GIP**; the bad answer came from
**AI Innovation Project** (`e7f14228`) — so resolving the right recording (and possibly the
right project) matters. `switch_meeting` resolves the project; `list_recordings` resolves
within it.

## NEXT (decided, planned, NOT started) — create_task → pm-agent reconcile bridge

Make the agent's `create_task` build a task template from the meeting MoM, let the
user review it (editable project), then hand it to pm-agent's `redmine_reconcile` loop
— instead of the user creating Redmine issues by hand. **Spec + 8-task TDD plan written
& committed** (`35ac010`):

- Spec: `docs/superpowers/specs/2026-06-08-create-task-reconcile-bridge-design.md`
- Plan: `docs/superpowers/plans/2026-06-08-create-task-reconcile-bridge.md`

Decisions locked: `create_task` bridges into the existing `pm_call`/`pm_await` loop
(new `pm_next_payload` kind `"reconcile"`); **two HITL gates** (GATE 1 local template
review → GATE 2 pm-agent's Redmine-write approval); project pre-filled from meeting
title, **editable** on GATE 1's card. Only one graph edge changes (`agent_execute →
agent` becomes conditional). `api/chat.py` unchanged (it already re-persists a fresh
pending action when a resume re-interrupts).

**Execute with `superpowers:executing-plans`, inline, TDD.** ⚠️ Task 6 deliberately
breaks two `test_agent_loop.py` create_task tests (approved create_task no longer
executes locally); Task 7 fixes them by switching to `send_email` — do 6→7 back-to-back.

## PENDING / NEXT

- **Verify the unified agent end-to-end LIVE** through `run_meeting.py` UI — only unit-tested
  (no DB suite). Needs the live blockers below cleared (psycopg + DB at head). Worth checking:
  auto-retrieve grounding quality, that gemma honors `tool_choice=auto` in real chats, and
  that the FE approve/reject card still works against the agent interrupt (same payload shape).
- **Wire `create_task` → pm-agent `redmine_reconcile`** (spec `2026-06-06-…`): the happy-path
  goal #2 (template → reconcile) is NOT in Task #8 — `create_task` currently only *produces*
  the structured task. Could later expose pm reconcile as a tool or post-approval step.
- **`transcript_segments` injection** — still deferred (spec §5); seam in `pm_call`.
- **pm_task lifecycle deltas (PARKED)**: Edit affordance on need_approval cards; clear
  cached `pm_task_id`/`pm_context_id` on terminal so a later message doesn't reuse an
  ended task; bump `PM_MAX_ROUNDS` (reconcile/batch need several pauses).
- **transcript_segments injection** — still deferred (spec §5); seam marked in `pm_call`.
- **Verify ChatPane end-to-end live** (still only typechecked; pm flow exercised via curl,
  not yet through `run_meeting.py` UI end-to-end).

## ⚠️ Live blockers / gotchas (will bite the next session)

1. **psycopg / libpq missing** — backend crashes on startup with
   `ImportError: no pq wrapper available`. Fix: `venv/bin/pip install "psycopg[binary]"`
   (or `sudo apt-get install -y libpq5`). The LangGraph checkpointer uses psycopg3.
2. **DB migration mismatch** — the shared remote DB (`180.93.182.45`, db `agents`, user `anhvd6`)
   is stamped at Alembic revision **`0015`**, but this repo only has migrations **`0001–0007`**.
   Files `0008–0015` exist in NO branch (local or remote) — they live only on whoever advanced
   that DB. Alembic errors: `Can't locate revision identified by '0015'`. Either get those `.py`
   files committed, or point `.env` `DATABASE_URL` at a local DB at head `0007`
   (`docker compose --profile local up -d` → `localhost:5435`).
3. **Startup banner lies** — "Postgres ● stopped — run docker compose" is cosmetic; it only checks
   for a *local* container `mee-postgres`. With a remote DB it's a false alarm; ignore it.
4. **venv was purged from git history** via `git filter-repo` (it had been committed → 50 MB pack →
   HTTP 413 on push). History was rewritten; SHAs changed. Backup bundle at
   `../mee-meeting-agent-prepurge.bundle`. `venv/` is now gitignored — never commit it.
5. **GateGuard hook** fact-forces before each Bash/Edit/Write (asks for facts, passes on retry).
   It adds overhead. To disable for an implementation burst: run with `ECC_GATEGUARD=off` or add
   `pre:edit-write:gateguard-fact-force` (and `pre:bash:gateguard-fact-force`) to `ECC_DISABLED_HOOKS`.

## Backend chat contract (for reference — what the FE now expects)

- `POST /api/chat/sessions` `{meeting_id, title?}` → `{id, meeting_id, title, created_at}`
- `POST /api/chat/sessions/{id}/messages` `{text}` → `{status:"complete", reply, ...}` OR
  `{status:"interrupted", pending_action_id, pending_action:{id, tool, args, rationale?, description?}}`
- `POST /api/chat/pending-actions/{id}/approve` `{edited_args?, reason?}` → `{status:"executed", reply}`
- `POST /api/chat/pending-actions/{id}/reject` `{reason?}` → `{status:"rejected", reply}`
