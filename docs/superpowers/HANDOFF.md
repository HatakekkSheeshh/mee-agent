# Session Handoff — Mee meeting-agent (feat/backend-agents)

**Branch:** `feat/backend-agents` · **Last updated:** 2026-06-10 · **Head:** `908bd5d`

Read this first when resuming. It captures state a fresh session can't infer from git alone.

## Kickoff message to paste into the new session

> Continue Mee on branch `feat/backend-agents`. Read CLAUDE.md and `docs/superpowers/HANDOFF.md`.
> Everything through the **force-grounding fix** is DONE and committed (unpushed): suite **92 green**
> (`ECC_GATEGUARD=off venv/bin/python -m pytest tests/meeting -q`), FE builds clean
> (`cd meeting_frontend_react && npm run build`).
> No active plan. Pick from the parked follow-ups below, or do the live smoke once the blockers
> clear. Parked: create_task **login↔display-name assignee filter** + **recording scoping**;
> reconcile **per-assignee chunking** (gateway timeout); the un-applied **modal `backdrop-filter`
> blur** perf fix; pm_task lifecycle deltas. All logged below.

## Current state (what's true right now)

- **Suite:** 92 passed (`ECC_GATEGUARD=off venv/bin/python -m pytest tests/meeting -q`). FE
  `npm run build` clean.
- **Backend chat = unified native tool-calling agent** (Path A, gemma). Flow:
  `load_context → classify_intent (binary pm_task|agent) → agent ⇄ agent_tools →
  (agent_approve interrupt → agent_execute) ↺ → save_reply`. Replay-safe (only `agent_approve`
  interrupts; side-effect tools run exactly once). `create_task` bridges into the pm-agent
  reconcile loop with two HITL gates (GATE 1 local template → GATE 2 pm Redmine-write). Reject of a
  side-effect tool is **terminal** (canned `REJECT_REPLY`, no LLM re-loop).
- **chat_graph is a package:** `meeting/graphs/chat_graph/` (`context/classify/agent/pm/builder/
  runner.py` + `__init__.py` facade re-exporting every public name incl. `repo`). Pure helpers live
  as `graphs/_chat_*.py` siblings. Tools are a package: `meeting/services/tools/` (one module per
  tool + local `@tool` decorator → `TOOLS`). Diagram: `docs/diagrams/chat_graph.mmd` (== live draw).
- **clear-chat-session** shipped: `repo.clear_chat_session`, `POST /api/chat/sessions/{id}/clear`
  (deletes messages+pending in place, best-effort `adelete_thread`), + a branded green confirm
  dialog in ChatPane. Only **unit-verified** (live blockers below).
- **Still only unit-tested — never run live through `run_meeting.py`** (psycopg + DB-revision
  blockers, see below). The whole agent/clear-chat path needs a live smoke once unblocked.

## DONE — force grounding for recording-scoped questions ✅

**Plan:** `docs/superpowers/plans/2026-06-10-force-grounding-recording-scoped.md` (executed in full).
Commits `838b28c` → `b9391b2` (+ probe `908bd5d`). The confirmed stale-summary bug (finding below)
is now fixed at two layers:

- **Task 0 (probe):** the MaaS gemma endpoint **honors `tool_choice="required"`** (returns a
  `tool_calls` message with empty content). Verified by `scripts/probe_tool_choice_required.py`. So
  the mechanical force shipped as written — no fallback needed.
- **Task 1/2 — `classify_intent` emits a `grounding` flag** (`"required"|"auto"`, threaded through
  `ChatState`, defaults `"auto"` when the model omits/garbles it). Content/recording questions →
  `"required"`; chit-chat / action / pm → `"auto"`.
- **Task 3 — agent forces a tool on round 0** when `grounding=="required"`: `tool_choice="required"`
  on the FIRST turn only, `"auto"` thereafter (so the post-tool answer turn finishes → loop still
  terminates). So gemma MUST read real data before it can answer.
- **Task 4 — prompt hardening** (`_agent_system_prompt`): narrowed the answer-direct escape hatch to
  explicitly EXCLUDE recording-scoped questions ("tóm tắt một phiên / Meeting N / nội dung một
  recording") — MUST call `list_recordings`/`recording_mom` first even if context looks sufficient.

**Residual risk:** still **unit-only** (92 green); never run live through `run_meeting.py` (psycopg +
DB-revision blockers below). Add a live smoke of "tóm tắt Meeting N" once the backend runs — confirm
a `tool_calls` line appears before the final answer and the date/content match the real recording.

## Parked follow-ups (NOT in the next plan)

- **create_task assignee filter is display-name only** — items carry `pic="Hiếu"`; "tạo task cho
  **hieunq3**" (a Redmine login) matches nothing. create_task also aggregates **project-level**
  `get_mom_action_items` and ignores a named recording ("trong Meeting 1") — no `recording_id`
  scoping; builds from `action_items` only (not decisions/commitments/blockers).
- **Reconcile chunking** — a 23-item reconcile timed out: the agentbase **gateway** dropped the
  connection mid-LLM-reconcile (`RemoteProtocolError`; NOT auth, NOT our 60s client timeout). Fix =
  one `message/send` **per assignee group**. The retry card won't help (same 23-item payload
  re-times-out).
- **`reason` field** on the create_task card is audit-only (persisted, not consumed downstream) —
  wire it into `_reconcile_text`/item descriptions, or remove it.
- **Modal `backdrop-filter` blur perf** — `.mee-modal-backdrop { backdrop-filter: blur(3px) }`
  re-rasterizes every frame while anything animates behind it; removing the blur (or pausing
  background animations while a modal is open) fixes the jank. One-line change, un-applied.
- **pm_task lifecycle deltas (PARKED):** edit affordance on need_approval cards; clear cached
  `pm_task_id`/`pm_context_id` on terminal; bump `PM_MAX_ROUNDS`; `transcript_segments` injection
  (spec §5, seam in `pm_call`).

## ⚠️ Live blockers / gotchas (will bite the next session)

1. **psycopg / libpq missing** — backend crashes on startup: `ImportError: no pq wrapper available`.
   Fix: `venv/bin/pip install "psycopg[binary]"` (or `sudo apt-get install -y libpq5`). The
   LangGraph checkpointer uses psycopg3.
2. **DB migration mismatch** — the shared remote DB (`180.93.182.45`, db `agents`, user `anhvd6`)
   is stamped at Alembic **`0015`**, but this repo only has **`0001–0007`**. `0008–0015` exist in no
   branch. Alembic errors `Can't locate revision '0015'`. Either get those `.py` files, or point
   `.env` `DATABASE_URL` at a local DB at head `0007` (`docker compose --profile local up -d` →
   `localhost:5435`).
3. **DB unavailable in this env** → `tests/meeting` use **fake sessions / direct endpoint calls**,
   not a live TestClient (see `test_clear_session.py`, `test_chat_api_pm.py`, `test_repo_recordings`).
   New DB-touching tests must follow that convention.
4. **Startup banner lies** — "Postgres ● stopped" only checks for a *local* container; ignore it
   with a remote DB.
5. **venv purged from git history** via `git filter-repo` (50 MB pack → HTTP 413). SHAs changed;
   backup bundle at `../mee-meeting-agent-prepurge.bundle`. `venv/` is gitignored — never commit it.
6. **GateGuard hook** fact-forces before each Bash/Edit/Write. Disable for a burst with
   `ECC_GATEGUARD=off` (or add `pre:bash:gateguard-fact-force` + `pre:edit-write:gateguard-fact-force`
   to `ECC_DISABLED_HOOKS`). `.claude/settings.local.json` has a local off env (gitignored).
7. **Untracked `cache/`** in the repo root (generated PNGs + pycaches) — junk, not committed; leave
   it or add to `.gitignore`.

## pm-agent integration — verified facts (live)

- Auth = **per-user Microsoft OIDC** Bearer token, env `PM_AGENT_URL` + `TOKEN_AUTHEN_PM_AGENT`;
  URL must end `/a2a/`. Client sends Bearer + X-API-KEY. **The token in `.env` is currently a UUID
  API key and returns `401` — it's stale/wrong; refresh it + restart (singleton + dotenv load once
  at startup).** A short curl probe in the git log of this session confirms 401.
- Resume MUST echo **both `taskId` and `contextId`** (else `-32603` context mismatch); captured in
  `PmAgentResult.context_id` / `ChatState.pm_context_id`.
- pm-agent surfaces auth via a `need_more_info` `/auth?url=…`; ends a need_more_info thread on the
  literal text `/cancel` (FE "Hủy" sends that).

## Backend chat contract (what the FE expects)

- `POST /api/chat/sessions` `{meeting_id, title?}` → `{id, meeting_id, title, created_at}`
- `POST /api/chat/sessions/{id}/messages` `{text}` → `{status:"complete", reply, ...}` OR
  `{status:"interrupted", pending_action_id, pending_action:{id, tool, args, rationale?, description?}}`
- `POST /api/chat/sessions/{id}/clear` → `{status:"cleared", session_id}` (404 if missing)
- `POST /api/chat/pending-actions/{id}/approve` `{edited_args?, reason?, approval_action?, text?}`
  → `{status:"executed", reply}` (may re-interrupt → fresh pending action)
- `POST /api/chat/pending-actions/{id}/reject` `{reason?}` → `{status:"rejected", reply}`

## Confirmed root-cause finding (the bug the next plan fixes)

"tóm tắt phiên họp meeting 1" answered with the WRONG date (04/06 vs real 03/06) and content from a
DIFFERENT meeting. Decisive log: `load_context (recent_msgs=10) → classify_intent →
[Node agent] final answer` with **NO `tool_calls` line** — the agent called zero tools, so it never
read the recording; it regurgitated a prior wrong summary sitting in `recent_messages` (seeded by
`_seed_agent_messages`), enabled by the `_agent_system_prompt` escape hatch *"khi đã đủ dữ liệu thì
trả lời trực tiếp (KHÔNG gọi tool)"*. A fresh session grounds correctly (empty history). clear-chat
= mitigation; force-grounding = the fix.

## Session timeline (chronological, all on feat/backend-agents)

1. CLAUDE.md authored; pm-agent A2A design + Phase-1 interactive ChatPane + Phase-2 backend pm
   branch (`pm_task`/`pm_call`/`pm_await`/`pm_reply`, `PmAgentClient`).
2. pm-agent auth verified live; `-32603` contextId fix; chat UX (markdown, pending cards, welcome
   banner, localStorage thread-per-meeting).
3. Task #8 — unified native tool-calling agent (Path A; gemma, not Qwen3). Option B —
   recording-scoped repo+tools (`list_recordings`/`recording_mom`).
4. create_task → pm reconcile bridge (2 gates); transport-error retry (`pm_error` card); editable
   grouped `CreateTaskCard`; `meeting/services/tools/` package.
5. chat_graph reorg Phase 1 + 2 (helpers extracted, DI seams, package split, facade).
6. Reject-terminal (option 3) + tests fixed → suite green. clear-chat-session (repo+endpoint+FE).
7. Chat-UI polish: branded green clear-confirm dialog; dropped tool-description bubble on interrupt;
   native-title tooltip fix; "action items" → "Việc cần làm"/"To-dos"; welcome-banner refactor
   (copy + icon-chip layout + data-driven + a11y); removed banner pulsing dot.
8. Force-grounding fix (plan 2026-06-10): probe confirmed gemma honors `tool_choice="required"`;
   classify emits a `grounding` flag; agent forces a tool on round 0 for recording-scoped questions;
   prompt escape-hatch narrowed. Suite 82 → 92.

## Reference artifacts (committed, self-contained)

- Plans: `plans/2026-06-10-force-grounding-recording-scoped.md` (NEXT),
  `plans/2026-06-09-clear-chat-session.md` (done), `plans/2026-06-09-create-task-reject-terminal.md`
  (done), `plans/2026-06-09-chat-graph-reorg.md` + `…-phase2-di-split.md` (done),
  `plans/2026-06-08-create-task-reconcile-bridge.md` + `…-unified-qa-tool-agent.md` (done).
- Specs: `specs/2026-06-09-clear-chat-session-design.md`,
  `specs/2026-06-08-create-task-reconcile-bridge-design.md`,
  `specs/2026-06-06-happy-path-retrieval-reconcile-design.md`,
  `specs/2026-06-02-pm-agent-a2a-chat-design.md`.
- `docs/pm-agent-graph.md` — pm-agent's full LangGraph. `CLAUDE.md` — repo architecture + gotchas.
