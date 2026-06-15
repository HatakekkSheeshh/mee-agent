# User-Scoped Chat Sessions (decoupled from meeting) — Design Spec

**Branch:** `feat/personalized-user-prompt` (or a fresh feature branch)
**Status:** Design approved 2026-06-15 (brainstorm). Spec for a fresh-session TDD build.
**Builds on:** the existing chat session/kickoff stack (`meeting/api/chat.py`,
`meeting/db/repositories.py`, `meeting/graphs/chat_graph/`, `meeting/services/kickoff.py`,
`meeting_frontend_react/src/store/AppContext.tsx`).

## Goal

Today a chat session is bound to one project (`chat_sessions.meeting_id`), so the
sidebar/threads are per-project. Change to **one user-scoped session model**: a
session belongs to the user and can span all projects. Add a **"New session"**
button (next to Clear) that creates a fresh session and switches to it, while
**old sessions stay stored** until the user explicitly removes them. A **sidebar
list** lets the user switch between sessions.

The agent still grounds on a project exactly like today — when the user selects a
project, the agent is told that `meeting_id` and scopes to it — but the
`meeting_id` now comes from the **live UI selection passed per turn**, not from a
column on the session. The session is *decoupled* from the meeting.

## Decisions (locked in brainstorm 2026-06-15)

1. **Sessions are user-scoped; `chat_sessions.meeting_id` is decoupled.** The
   column becomes **nullable** and is no longer how a turn is grounded. Existing
   rows keep their (now-ignored) binding — no data backfill.
2. **Project grounding is per-turn, from the UI selection.** The chat **send**
   request carries the currently-selected `meeting_id`; the agent's context
   loader grounds on *that* project for the turn (same grounding behavior as
   today). Switching project mid-session re-grounds. **No project selected →
   answer without project grounding** (general).
3. **Kickoff fires on every new/empty session.** Trigger is unchanged
   (`if the thread already has messages → skip`); it is already role-based
   (`user.role`), so it works without any project binding. "New session" → empty
   thread → Mee greets with the role-persona kickoff.
4. **Sidebar session list.** Lists the user's sessions ordered most-recent-first;
   click to switch; each has a remove (✕) action (with confirm). "New session"
   prepends a fresh session and switches to it. On app load, the
   **most-recently-active** session opens.
5. **Remove = hard delete.** Purge the session's messages + pending actions + the
   LangGraph checkpoint thread + the `chat_sessions` row. Distinct from the
   existing **Clear** (which empties the thread but keeps the row + checkpoint).
6. **Activity ordering.** A `last_active_at` timestamp drives both the load
   target and sidebar order, bumped whenever a message is added. *Reuse an
   existing timestamp column if one already serves this; the plan confirms.*

## Components (small, testable — TDD; suite `tests/meeting`, `asyncio_mode=auto`)

### A. DB — `chat_sessions` schema
- Alembic `0022` (idempotent/guarded like `0021`): make `chat_sessions.meeting_id`
  **nullable**; add `chat_sessions.last_active_at timestamptz NULL` *(only if no
  existing column already fits — confirm against the model in the plan)*.
- `ChatSession` model: `meeting_id` Optional; add `last_active_at` if introduced.

### B. Repositories — `meeting/db/repositories.py`
- `create_chat_session(session, user_id, ...)` — **no required `meeting_id`**.
- `list_chat_sessions_for_user(session, user_id)` — order by `last_active_at`
  desc (then `created_at`) for the sidebar + load target.
- `add_chat_message(...)` — bump the session's `last_active_at` on write.
- `delete_chat_session(session, session_id)` — **hard delete**: messages +
  pending actions + row. (Checkpoint-thread purge is done by the API layer, which
  already owns the checkpointer handle — mirror `clear_session`'s purge call.)

### C. API — `meeting/api/chat.py`
- Create-session endpoint: no `meeting_id` needed.
- `GET /sessions` (list for sidebar) — returns id, title/label if any,
  `last_active_at`, message-count or last-message preview as needed by the FE.
- `DELETE /sessions/{id}` (**remove**) — hard delete via repo + purge the
  LangGraph checkpoint thread (reuse the existing purge used by Clear). Idempotent.
- **Send/message endpoint**: accept an optional `meeting_id` (selected project) in
  the request; thread it into the graph/context loader for per-turn grounding.
- **Kickoff endpoint**: drop any dependence on a session→project binding (it
  already greets from `user.role`).

### D. Agent context grounding — `meeting/graphs/chat_graph/context.py`
- `load_context` grounds on the **passed-in `meeting_id`** (selected project) for
  the turn instead of a session-bound project. None → no project grounding.
  *The exact signature/wiring is confirmed in the plan.*

### E. Frontend — `meeting_frontend_react/src/store/AppContext.tsx` + chat hook
- Sidebar session list: fetch on load, switch on click, ✕ remove (with confirm).
- "New session" button next to Clear → create session → switch → kickoff fires.
- Track the **selected project** as UI state; send it as `meeting_id` with each
  chat message. Selecting a project re-grounds subsequent turns.
- On load: fetch sessions → open the most-recently-active.

## Error handling
- Delete is idempotent (deleting a missing/foreign session → 404, no partial
  state). Per the existing pattern, the checkpoint purge is best-effort and must
  not 500 the delete.
- A turn with no selected project must not error — it grounds generally.

## Testing (TDD)
- repo: `create_chat_session` without `meeting_id`; `list_chat_sessions_for_user`
  ordering by `last_active_at`; `add_chat_message` bumps `last_active_at`;
  `delete_chat_session` purges messages + pending actions + row.
- api: `DELETE /sessions/{id}` hard-deletes + purges checkpoint; send endpoint
  passes `meeting_id` through; kickoff works with no project.
- grounding: `load_context` uses the passed `meeting_id`; None → no project data.
- migration `0022`: single head; `meeting_id` nullable; idempotent guard.

## Migration / run
- `0022` authored idempotently (guarded). User applies via `alembic upgrade head`.
- Existing sessions remain listed (their old `meeting_id` is ignored).

## Out of scope
- Renaming/titling sessions, session search, cross-device realtime sync.
- Soft-delete/recovery (we chose hard delete).
- Changing the kickoff greeting content (role-persona kickoff is unchanged).
