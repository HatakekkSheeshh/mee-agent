# Design: capture chat-derived knowledge into agent memory

Status: IMPLEMENTED — 2026-06-16
Branch: `feat/personalized-user-prompt`

## Problem
The chat agent **read** memory (the distilled `project_facts/mee-user` projection,
recalled as `project_memory`) but never **wrote** it. The only writer of durable
agent memory was `mom_graph.py` (MoM → memory_events). So a fact a user stated in
chat — or one the agent deduced — persisted only as `chat_messages`, wiped by
"Xóa hội thoại". There was no "remember this" capability, and no way to retract one.

## Goal
Let knowledge introduced in chat become durable, recallable agent memory — per-user
or per-project — and let the user turn an individual fact off (and back on) without
polluting memory or requiring deletes (AgentBase DELETE is 403).

## Decisions (locked)
- **Store:** AgentBase memory-records (not `memory_events`). Recall surfaces in the
  prompt automatically via `load_context`.
- **Actor granularity (read == write):**
  - `scope="user"` → `user_prefs/<ms_oid>` (per-user; e.g. "gọi tôi là Ronaldo").
  - `scope="project"` → `project_facts/<meeting_id>` (shared across the project's
    users; partitioned by meeting, **never** `ms_oid` — that would silo project
    knowledge per user). Orthogonal to the distillation's `project_facts/mee-user`,
    so a remembered fact never shadows `search_project_record`.
- **Trust:** both user-asserted and agent-deduced facts are stored.
- **Mechanism:** model-invoked tools, **auto-run** (no HITL). The agent decides;
  the AgentBase write is dispatched fire-and-forget so the turn never blocks.
- **Retract:** soft, via a newer record — no delete.

## Architecture

### Fact record (`meeting/memory_client.py`)
A remembered fact is one AgentBase record whose first line is a control marker:

```
[mee-fact scope=<user|project> key=<hash> active=<1|0> author=<ms_oid> session=<sid>]
<body text>
```

- `key = fact_key(text)` — sha1 of the normalized (lowercase + whitespace-collapsed)
  **raw** text. Same key ⇒ same logical fact, so a later `active=0` supersedes an
  earlier `active=1` (and vice-versa) under newest-wins.
- `author` (Entra OID) + `session` (chat session id) make every fact auditable.
- Distinct from the `[mee-sync …]` distillation blob; the two never collide.
- Helpers: `fact_key`, `build_fact_record_text`, `parse_fact_marker` (tolerant of
  field order + legacy markers without key/active → active), `strip_fact_marker`,
  `fact_namespace`, `insert_fact_record(active=, key=)`, and `list_fact_records`.
- `list_fact_records` is the read engine: browse namespace → newest-first → collapse
  **newest-wins per key** → drop facts whose newest record is a tombstone → return
  active bodies. Insert-only, newest-wins (DELETE is 403).

### Tools (`meeting/services/tools/remember_fact.py`)
Both `side_effect=False` (auto-run), both background-write via `_dispatch_write`
(loop executor; sync fallback for scripts), both best-effort (`MEMORY_ID` unset →
`{"status":"disabled"}`, never raises). A shared `_resolve_target` maps scope →
(namespace, author_oid, project_title).

- **`remember_fact(text, scope="project")`** — stores/re-activates a fact. Project
  facts are decorated `(Dự án <title>) <text>` for readable recall (title resolved
  from `meetings.id`; best-effort). Dedup: skips if the identical fact is already
  active (a forgotten fact isn't active → re-insert revives it).
- **`forget_fact(text, scope="project")`** — writes a newer `active=0` tombstone
  keyed by the fact's text; recall then hides it.

`meeting_id` is injected server-side (stripped from the LLM schema like other tools);
`session_id` is injected from `state["session_id"]` for `_SESSION_AWARE_TOOLS` in
`agent_tools` (not in the schema, so the LLM never supplies it).

### Recall (`meeting/graphs/chat_graph/context.py`)
`load_context` recalls (best-effort, off the event loop):
- user facts from `user_prefs/<ms_oid>` — **even with no meeting bound** (a danh-xưng
  must surface in general chat too);
- project facts from `project_facts/<meeting_id>` — for the turn's meeting.

They're appended to `project_memory` as a labeled **"Ghi nhớ"** block, capped at
`MAX_RECALLED_FACTS = 20` (newest first) so the prompt doesn't bloat over time.

### Prompt (`meeting/graphs/_chat_prompts.py`)
`_agent_system_prompt` nudges the agent to call `remember_fact` on durable
assertions/deductions and `forget_fact` when the user says to stop using one.

## End-to-end ("call me Ronaldo")
1. *"gọi tôi là Ronaldo"* → `remember_fact(text, scope="user")` → background write to
   `user_prefs/<ms_oid>` (`active=1`).
2. Next session → `load_context` recalls it → "Ghi nhớ" block → agent greets Ronaldo.
3. *"đừng gọi tôi là Ronaldo nữa"* → `forget_fact(...)` → newer `active=0` → hidden.
4. *"gọi tôi là Ronaldo lại"* → `remember_fact(...)` → newest `active=1` → back.

## Guardrails / non-goals
- No HITL gate (auto-run, per product decision) → dedup + soft-forget are the
  pollution controls. Marker records author + session for audit.
- Best-effort everywhere — capture/recall failures never break a chat turn.
- Embedding/semantic search over facts is out of scope (recall is namespace browse).

## Tests
- `test_memory_client_facts.py` — marker build/parse (incl. legacy + key/active),
  `fact_key` normalization, namespace resolution, insert, list newest-wins +
  forget/reactivation.
- `test_tools_remember_fact.py` — scope routing, title resolution, dedup, background
  dispatch, disabled/error paths; `forget_fact` tombstone + side_effect=False.
- `test_load_context_facts.py` — user/project recall, cap, no-facts.
- `test_agent_tools_session_inject.py` — session_id plumbing.
- `test_chat_project_memory.py` — prompt nudge.

## Related
- docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md (distillation projection)
- docs/superpowers/specs/2026-06-14-oid-role-persona-design.md (`user_prefs/<oid>`)
