# Session Handoff — Mee meeting-agent

**Branch:** `feat/mail-tool` · **Last updated:** 2026-06-11 · **Head:** `047fa8d`
**Suite:** 117 green (`ECC_GATEGUARD=off venv/bin/python -m pytest tests/meeting -q`) · FE `npm run build` clean.

Read this first when resuming. It captures state a fresh session can't infer from git alone.

## Kickoff message to paste into the new session

> Continue Mee on branch `feat/mail-tool`. Read CLAUDE.md and `docs/superpowers/HANDOFF.md`.
> Everything below "DONE" is committed: suite **117 green**, FE builds clean.
> **NEXT PLAN: integrate agentbase Memory into the chat agent** (see the NEXT section —
> a write-only client already exists in `meeting/memory_client.py`; design read+write
> chat memory via brainstorming first). Also pending: live smoke of SSE/zoom/chunked
> reconcile, the Microsoft Graph tool suite (designed, gated on Azure app registration),
> and the gemma multi-turn prompt hardening (test in place, prompt fix not applied).

## NEXT PLAN — agentbase Memory for the chat agent

**Objective:** give the chat agent persistent memory via the **AgentBase Memory Service**
(`https://agentbase.api.vngcloud.vn/memory/...`) — recall user/project facts across chat
sessions, not just within one LangGraph thread.

**What already exists (verified in repo):**
- `meeting/memory_client.py` — a stdlib-only, WRITE-ONLY AgentBase client. Auth = GreenNode IAM
  client-credentials (`GREENNODE_CLIENT_ID`/`GREENNODE_CLIENT_SECRET` env or `.greennode.json`
  fallback; token POST to `iam.api.vngcloud.vn/accounts-api/v2/auth/token`, cached with JWT-exp).
  Posts conversational events to
  `POST /memory/memories/{MEMORY_ID}/actors/{actor_id}/sessions/{session_id}/events`
  with payload `{"payload": {"type":"conversational","role","message"}}`. Hardcoded
  `actor_id="mee-user"`; `MEMORY_ID` from env. Used ONLY by the legacy MoM flow
  (`app.py:344`, background thread, failures swallowed).
- `meeting/services/memory_service.py` — the LOCAL hybrid retrieval (pgvector + tsvector + RRF)
  over `memory_events`; surfaced to the agent as the `retrieve` tool. Separate system — decide
  the relationship explicitly.

**Open questions to resolve at kickoff (brainstorm before code):**
1. **Read API** — memory_client has no retrieval call. Find the AgentBase Memory search/recall
   endpoint (docs or probe; same Bearer token should work). Without read, "memory" is a black hole.
2. **What to store from chat** — full turns? distilled facts (user preferences, recurring
   assignees, project glossary)? Distillation needs an LLM step — where (save_reply? background)?
3. **Where to hook** — natural seams: `load_context` (inject recalled memory into
   `meeting_context`/system prompt), a new read tool (`recall_memory`) next to `retrieve`,
   and `save_reply` (persist the turn). Replay-safety: writes must not live in interrupting nodes.
4. **Identity** — `actor_id` is hardcoded "mee-user"; the app runs on `dev_user`. Per-user memory
   needs a real actor key (ties into the future O365 login).
5. **Local `memory_service` vs agentbase** — complement (agentbase = cross-session conversational,
   local = meeting-content RAG) or migrate? Recommend complement first; no migration in v1.

**Suggested first steps:** probe the AgentBase Memory read API with the existing token flow
(small script in `scripts/`, like `probe_pm_list_issues.py`); then brainstorming → spec → plan.

## DONE (all committed, chronological this branch)

1. **Chat UX streaming** (`3e17f4f…85c7a50`, spec `specs/2026-06-10-chat-ux-streaming-design.md`):
   SSE endpoint `POST /api/chat/sessions/{id}/messages/stream` (steps + terminal frame; own
   `AsyncSessionLocal` — a Depends session dies before a StreamingResponse body runs; blocking
   endpoint kept as fallback). FE: live activity trace → collapses into the reply (`<details>`),
   stop button (AbortController), `ActionArgsCard` (generic editable HITL card, edits →
   `edited_args`), reject/stop note lines, copy button, modal blur removed.
2. **3 parked follow-ups** (`b5ddcde`): `assignee_matches` (login↔display-name, diacritic-
   insensitive both-way substring); create_task `recording_id` scoping; **chunked reconcile**
   (`_reconcile_payloads` per assignee group, sub-chunk at `MAX_RECONCILE_ITEMS=8`;
   `pm_queue`/`pm_replies` state; `pm_reply` drains queue with a FRESH pm task per group via
   `route_after_pm_reply`; replies joined with `---`; one GATE-2 card per group; reject/error
   abandons the rest of the queue — documented); `reason` → "Ghi chú của người duyệt: …" in
   reconcile text.
3. **pm-agent routing probe** (`471dd1f`): "liệt kê issue trong project AI Innovation Project"
   verified end-to-end OK (classify→pm_task stable ×2; pm-agent returns the correct
   project-scoped list given verbatim text). `scripts/probe_pm_list_issues.py`.
4. **No internal identifiers in UI** (`023bc1c`): `toolLabel`/`argLabel` helpers in `i18n.ts`
   (`tool.*` ×7 = full registry coverage, `arg.*` incl. future schedule_meeting fields; raw-name
   fallback). Interrupt hint no longer falls back to the internal English tool description.
5. **Magnify cards** (`bbc14aa`): `ZoomCard` wrapper around all five pending-card branches —
   CSS-only fixed overlay `min(720px,92vw)` (no portal → edits survive toggling), Esc/backdrop/
   button collapse, auto-collapse on pending change. No backdrop-filter (blur-jank lesson).
6. **Multi-turn context regression test** (`047fa8d`, `tests/meeting/test_multiturn_context.py`):
   live bug "email đến andvd6" → supply subject/body next turn → agent re-prompts instead of
   merging into send_email. Tests PASS ⇒ harness delivers turn-1 context to the LLM; **the loss
   is gemma/prompt-side. Fix = `_agent_system_prompt` hardening ("khi user bổ sung thông tin còn
   thiếu cho hành động đã bàn → gọi tool với thông tin gộp, KHÔNG hỏi lại") — NOT APPLIED yet.**

## Parked / pending (not in the next plan)

- **Microsoft Graph tool suite — designed, not built.** One `meeting/services/graph/` layer
  (thin httpx, NO msgraph-sdk) + two seams: `GraphTokenProvider` (Dev→logs would-be request;
  Delegated device-code once an Azure app exists; O365 web later) and `RecipientResolver`
  (placeholder now; `transcript-flow-improvements` branch will supply name→email). Tools: real
  `send_email` (**currently still MOCK**, `tools/send_email.py`) + `schedule_meeting`
  (findMeetingTimes + create event with Teams link; room = manually-edited card placeholder —
  avoids admin-gated `Place.Read.All`). Facts: VNG tenant `7c112a6e-10e2-4e09-afc4-2e37bc60d821`;
  `TOKEN_AUTHEN_PM_AGENT` is the pm-agent key, NOT a Graph cred; findMeetingTimes +
  `/me/onlineMeetings` are delegated-only; all needed delegated scopes are user-consentable —
  **gate = can the user register an Azure app? (unverified)**.
- **Live smoke checklist** (first session with the backend running): SSE steps arrive
  incrementally through the Vite proxy (not buffered) + stop button mid-turn; zoom cards
  click-through; multi-assignee create_task → sequential GATE-2 cards + joined reply;
  "tóm tắt Meeting N" force-grounding (tool_calls line before answer).
- **pm_task lifecycle deltas:** edit affordance on need_approval cards; clear cached
  `pm_task_id`/`pm_context_id` on terminal; bump `PM_MAX_ROUNDS`; `transcript_segments`
  injection (spec §5, seam in `pm_call`).
- create_task still builds from `action_items` only (not decisions/commitments/blockers).

## ⚠️ Live blockers / gotchas

1. **DB migration drift:** repo head `0015`; shared remote DB (`180.93.182.45`, db `agents`) is
   stamped `0016` (recurring drift). RUN the backend WITHOUT `alembic upgrade head` — app uses
   asyncpg+ORM; ahead-DB is fine if `0016` is additive. Fix = get `0016_*.py` from the DB owner.
2. **No DB in this dev env** → `tests/meeting` use fakes/direct endpoint calls (see
   `test_clear_session.py`, `test_agent_loop.py`); new DB-touching tests follow that convention.
   `tests/` root (`test_server.py` etc.) tests the legacy whisper_live system — ignore.
3. **Startup banner lies** — "Postgres ● stopped" only checks a local container.
4. **GateGuard hook** fact-forces Bash/Edit/Write — disable bursts with `ECC_GATEGUARD=off`.
5. **venv purged from git history** (filter-repo; SHAs changed; backup bundle at
   `../mee-meeting-agent-prepurge.bundle`). Never commit `venv/`.
6. **A stash exists**: `stash@{0}` = accidental pre-SSE ChatPane revert (recovered 2026-06-11);
   drop when confident. `docs/diagrams/chat_graph.mmd` has an uncommitted USER edit — don't revert.

## pm-agent integration — verified facts (live)

- Auth: Bearer (+X-API-KEY sent too), env `PM_AGENT_URL` (must end `/a2a/`) +
  `TOKEN_AUTHEN_PM_AGENT`. Working as of 2026-06-11 (probe returned live issue lists).
- Resume MUST echo both `taskId` + `contextId` (else `-32603`). need_more_info threads end on
  literal `/cancel`. Reconcile = text + `data_part {kind:"reconcile_items", project, items}`.

## Backend chat contract (FE-facing)

- `POST /api/chat/sessions` `{meeting_id, title?}` → `{id, ...}`
- `POST /api/chat/sessions/{id}/messages` `{text}` → `{status:"complete",...}` |
  `{status:"interrupted", pending_action_id, pending_action}`
- `POST /api/chat/sessions/{id}/messages/stream` — SSE: `{type:"step",...}`* then
  `{type:"complete"|"interrupted"|"error", ...}` (same payloads as blocking)
- `POST /api/chat/sessions/{id}/clear` → `{status:"cleared"}`
- `POST /api/chat/pending-actions/{id}/approve` `{edited_args?, reason?, approval_action?, text?}`
  → `{status:"executed", reply}` (may re-interrupt) · `/reject` `{reason?}` → `{status:"rejected"}`

## Reference artifacts

- Specs: `specs/2026-06-10-chat-ux-streaming-design.md` (+ all earlier specs/plans, all executed).
- `docs/pm-agent-graph.md` — pm-agent's LangGraph. `docs/diagrams/chat_graph.mmd` — our graph
  (user is mid-edit). `CLAUDE.md` — architecture + gotchas.
