# Plan: capture chat-derived knowledge into agent memory

Status: PROPOSED (not started) — 2026-06-16

## Problem
Today the chat agent **reads** memory (`project_facts` SEMANTIC strategy, recalled
as `project_memory`) but never **writes** it. The only writer of `memory_events`
is `mom_graph.py` (MoM → action_items/decisions/commitments/blockers). So when a
user states a new fact in chat, or the agent deduces one, it persists ONLY as
`chat_messages` (session history, wiped by "Xóa hội thoại") — never into durable
agent memory. No "remember this" capability exists.

## Goal
Let knowledge introduced in chat (user-provided or agent-deduced) become durable,
recallable memory for the project — without polluting it with chatter.

## Sketch (to refine)
- A side-effect tool `remember_fact(text, scope=project|user)` the agent calls when
  the user asserts a durable fact ("X phụ trách module Y", "deadline dời sang …").
  HITL-gated like other side-effect tools so the user approves what gets stored.
- Write path options:
  1. Insert into `memory_events` (same store MoM uses) → flows to AgentBase via the
     existing `sync_project` projection. Reuses retrieval + embeddings.
  2. Or insert directly into AgentBase `project_facts/{actor}` (SEMANTIC) namespace
     via `memory_client` (mirror `sync_memory.py` write contract).
  - Prefer (1): single source of truth, existing sync handles the projection.
- Guardrails: only on explicit assertion (not every message); dedup; attribute
  source (chat session id + author) so it's auditable; respect the insert-only,
  newest-wins AgentBase contract (DELETE is 403 — see agent-memory-sync note).

## Actor-granularity decision (AgentBase namespace = `{strategy}/{actorId}`)
Match the actor key to the memory's PURPOSE — read + write must use the SAME actor:
- **Per-user knowledge** ("things THIS user told the agent", persona/prefs) →
  actor = **`ms_oid`** (Entra OID): stable, unique, URL-safe GUID, already on
  `User.ms_oid`. Namespace `user_prefs/<ms_oid>` (or `project_facts/<ms_oid>` if
  facts are private to the user).
- **Shared project knowledge** (facts teammates on the same meeting should also
  recall) → do NOT use `ms_oid` — it silos memory per user. Keep the shared actor
  (`mee-user`) or partition by **`meeting_id`**.
- Today `DEFAULT_ACTOR_ID = "mee-user"` (shared) and `project_facts` is shared by
  design; `user_prefs/{OID}` is the per-user persona namespace.
- Threading: pass the chosen actor through BOTH `memory_client.search_project_record`
  and the sync write — both currently default to `mee-user`.

## Open questions
- Is chat-captured knowledge per-user (ms_oid) or shared project fact (meeting_id)?
  → drives the actor key per the decision above.
- Trust: should agent-*deduced* facts be stored, or only user-*asserted* ones?
- Does it belong as a tool the model invokes, or a post-turn extraction pass?

## Related
- docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md
- Deferred "learned style persona" feature (user_prefs/{OID}).
