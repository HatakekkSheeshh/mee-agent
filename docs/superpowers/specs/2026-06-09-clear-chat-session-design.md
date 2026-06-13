# Clear chat session (in-place) — Design

**Date:** 2026-06-09 · **Branch:** `feat/backend-agents`

## Problem

Each project (meeting) is bound to exactly one chat session. Over time that session's
history accumulates, and a confirmed bug results: `load_context` loads the last 10
`chat_messages` → `_seed_agent_messages` seeds them into the LLM prompt → gemma, seeing a prior
(possibly wrong) summary already in context, hits the system-prompt escape hatch *"khi đã đủ dữ
liệu thì trả lời trực tiếp (KHÔNG gọi tool)"* and answers **without calling any tool** (proven by
a live log: `[Node agent] final answer` straight after `classify_intent`, no `tool_calls` line).
It then regurgitates the stale summary — wrong date (04/06 vs real 03/06) and content from a
different meeting. A fresh session grounds correctly because its history is empty.

Users need a way to reset a project's chat **without** breaking the 1:1 project↔session model.

## Goal

A "Clear chat" action that wipes a session's contents **in place** — same `session_id`, same
`meeting_id` binding — giving the agent a clean `recent_messages` (so it re-grounds via
`list_recordings`/`recording_mom`) and dropping any dangling LangGraph interrupt.

## Decisions (locked)

- **In place** — keep the `chat_sessions` row, its `id`, and `meeting_id`. Project = one session.
- **Full wipe** — delete `chat_messages` + `pending_actions` for the session AND purge the
  LangGraph checkpoint thread (`thread_id == str(session_id)`).
- **UX** — a "Xóa hội thoại" button in the ChatPane header → confirmation dialog → on success
  empty the thread + re-show the welcome banner.
- No migration, no archive, no undo, no multi-session (YAGNI).

## Backend

### Repo — `meeting/db/repositories.py`
```python
async def clear_chat_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    """Delete a session's messages + pending actions in place (keeps the session row)."""
    await session.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await session.execute(delete(PendingAction).where(PendingAction.session_id == session_id))
    # optional: bump last_activity_at on the session
```
- Delete `pending_actions` rows rather than status-flagging — the `status` CHECK constraint has
  no "cancelled" value, and a cleared session has no live interrupt to track.

### Endpoint — `meeting/api/chat.py`
`POST /api/chat/sessions/{session_id}/clear` (reuse the same current-user / ownership dependency
as `GET /sessions/{session_id}`):
1. Load session; `404` if missing or not owned by the user.
2. `await repo.clear_chat_session(db, session_id)`.
3. Purge the checkpoint thread — **best-effort**:
   ```python
   try:
       await get_checkpointer().adelete_thread(str(session_id))
   except Exception:
       logger.warning("clear: checkpoint purge failed for %s", session_id, exc_info=True)
   ```
   (`AsyncPostgresSaver.adelete_thread` exists — langgraph-checkpoint 4.1.1. The grounding fix
   depends on the `chat_messages` deletion, not the checkpoint, so a purge failure is non-fatal.)
4. Return `{"status": "cleared", "session_id": str(session_id)}`.

## Frontend

- **`src/api/client.ts`** — `chat.clear(sessionId: string): Promise<{status: string; session_id: string}>`
  → `POST /api/chat/sessions/${sessionId}/clear`.
- **`src/components/ChatPane.tsx`** — "Xóa hội thoại" button in the pane header. onClick →
  confirm dialog → `chat.clear(sessionId)` → on success: `setMessages([])`, re-show the welcome
  banner, and **clear the localStorage thread cache for this meeting** (keep the stored session
  id — clear is in place). Busy + error states like the existing send flow.
- **`src/i18n.ts`** — `chat.clear`, `chat.clearConfirm`, `chat.cleared` (VI + EN).

## Data flow (why it fixes the bug)

clear → `chat_messages` empty → `load_context` returns `recent_messages=[]` →
`_seed_agent_messages` seeds only the new user message → no stale summary in context → the agent
calls `list_recordings`/`recording_mom` and grounds on the real `mom_json`. Purging the checkpoint
thread also drops carried `agent_messages` and any dangling interrupt.

## Error handling

- `404` session missing / not owned.
- Checkpoint purge failure → logged, non-fatal (DB deletion already done; response still `cleared`).
- FE surfaces a generic error toast on non-2xx; the thread is only emptied on success.

## Testing (`tests/meeting`)

- Seed a session with several `chat_messages` + one `pending_actions` row; call the clear path
  (repo fn directly, and/or the endpoint with a mocked checkpointer); assert both tables are empty
  for that session and the session row still exists; assert `adelete_thread(str(session_id))` was
  invoked (mock the checkpointer). Suite stays green.

## Out of scope
- Forcing grounding for recording-scoped questions (the *deeper* fix for the summary bug) — a
  separate follow-up; clear-chat is the user-facing mitigation.
