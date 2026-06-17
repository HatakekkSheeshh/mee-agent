# Clear chat session (in-place) — Implementation Plan

> Execute with `superpowers:executing-plans`, inline, TDD. Run with `ECC_GATEGUARD=off`.
> Safety net: `venv/bin/python -m pytest tests/meeting -q` must stay green (currently 77 passed;
> this plan ADDS tests). **Spec:** `docs/superpowers/specs/2026-06-09-clear-chat-session-design.md`.

**Branch:** `feat/backend-agents`. **Goal:** a "Clear chat" action that wipes a session's
messages + pending actions + LangGraph checkpoint **in place** (same `session_id` / `meeting_id`),
so `recent_messages` resets and the agent re-grounds.

## Task 1 — repo `clear_chat_session` (TDD)
File: `meeting/db/repositories.py`.
- Test first (`tests/meeting/test_clear_session.py`, fake/in-memory session or the existing test
  DB pattern used by other repo tests): seed a `ChatSession` + 3 `ChatMessage` + 1 `PendingAction`;
  call `clear_chat_session(db, session_id)`; assert `list_chat_messages` → `[]`, no pending rows
  remain, and the `ChatSession` row still exists.
- Implement: `async def clear_chat_session(session, session_id) -> None` issuing
  `delete(ChatMessage).where(session_id==…)` + `delete(PendingAction).where(session_id==…)`
  (import `delete` from sqlalchemy). Keep the session row.
- `pytest tests/meeting -q` green. Commit `feat(chat): repo.clear_chat_session (delete messages + pending in place)`.

## Task 2 — `POST /sessions/{id}/clear` endpoint (TDD)
File: `meeting/api/chat.py`.
- Test first: call the endpoint for a seeded session with a **mocked checkpointer**
  (`get_checkpointer().adelete_thread` as an AsyncMock); assert `200 {"status":"cleared"}`,
  messages+pending gone, `adelete_thread` called with `str(session_id)`; assert `404` for a
  missing / non-owned session. Mirror the auth/dependency + TestClient setup already used by the
  other chat-API tests (`test_chat_api_pm.py`).
- Implement the route per the spec: ownership check (reuse the dep used by `GET /sessions/{id}`)
  → `repo.clear_chat_session` → best-effort `await get_checkpointer().adelete_thread(str(id))`
  wrapped in try/except (log, non-fatal) → return `{"status":"cleared","session_id":...}`.
- Green. Commit `feat(chat): POST /sessions/{id}/clear endpoint (purge messages+pending+checkpoint)`.

## Task 3 — FE client + ChatPane button + i18n
Files: `meeting_frontend_react/src/api/client.ts`, `.../components/ChatPane.tsx`, `.../i18n.ts`.
- `client.ts`: add `clear(sessionId)` → `POST /api/chat/sessions/${sessionId}/clear`, typed
  `Promise<{ status: string; session_id: string }>`.
- `ChatPane.tsx`: "Xóa hội thoại" button in the pane header → `window.confirm`/dialog
  (`t('chat.clearConfirm')`) → on confirm call `chat.clear(sessionId)`; on success `setMessages([])`,
  re-show welcome banner, and clear the meeting's localStorage thread cache (KEEP the session id).
  Busy/disabled + error states matching the send flow. (Component is open in the IDE.)
- `i18n.ts`: `chat.clear` ("Xóa hội thoại" / "Clear chat"), `chat.clearConfirm`, `chat.cleared`
  (VI + EN).
- Verify `cd meeting_frontend_react && npm run build` (or `tsc --noEmit`) is clean.
- Commit `feat(chat): clear-chat button in ChatPane + client + i18n`.

## Self-review / risk
- No schema change → no migration.
- In-place keep of `session_id` means the FE's localStorage thread-per-meeting binding is
  unaffected (only the cached *messages* are cleared).
- Checkpoint purge is best-effort; the grounding reset relies on the `chat_messages` deletion.
- Does NOT address the deeper "force grounding for recording-scoped questions" fix — separate
  follow-up (see HANDOFF finding #3).
