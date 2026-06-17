# User-Scoped Chat Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple chat sessions from a single project so a session is user-scoped and spans all projects, with per-turn project grounding from the live UI selection, a sidebar session list, a "New session" button, and hard-delete remove.

**Architecture:** The backend already stores sessions per-user with a `last_activity_at` column, orders by it, bumps it on each message, and allows a nullable `meeting_id` (migration `0002` never set `NOT NULL`; the model declares it `Optional`). The remaining backend work is: a guarded `0022` migration that *ensures* `meeting_id` is nullable (idempotent safety for the drifted prod DB), a hard-delete repo helper + `DELETE /sessions/{id}` endpoint, threading a per-turn `meeting_id` from the send request into the graph, and grounding `load_context` on that per-turn `meeting_id` instead of a session column. The frontend replaces its per-meeting session refs with a user-scoped session list (sidebar + new/remove) and sends the selected project's `meeting_id` with every turn. Kickoff is already role-based and project-agnostic — no change needed.

**Tech Stack:** FastAPI, SQLAlchemy (async, asyncpg), Alembic, LangGraph, pytest (`asyncio_mode=auto`), React 18 + TS + Vite.

---

## Pre-flight (read before starting)

**Test command** (offline — no live DB; tests use fake/recording sessions + monkeypatch):
```bash
DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting -q
```
Baseline: **313 passed**. Each backend task ends green.

**GateGuard:** the first Bash command and every Edit/Write is gated. When the gate fires, present (1) the user request in one sentence, (2) what the command/edit produces, then retry. Or run with `ECC_GATEGUARD=off`.

**Key facts discovered (do not re-derive):**
- `meeting/db/models.py:331` — `ChatSession.meeting_id` is `Mapped[Optional[uuid.UUID]]` (already nullable; FK `ondelete="SET NULL"`).
- `meeting/db/models.py:338` — `ChatSession.last_activity_at` already exists.
- `meeting/db/repositories.py:618` `create_chat_session` already takes `meeting_id: Optional = None`.
- `meeting/db/repositories.py:637` `list_chat_sessions_for_user` already orders by `last_activity_at.desc()`.
- `meeting/db/repositories.py:648` `add_chat_message` already bumps `last_activity_at`.
- `meeting/db/repositories.py:685` `clear_chat_session` deletes messages + pending actions, keeps the row.
- `meeting/api/chat.py:344` `send_message` currently grounds on `chat.meeting_id`; `meeting/api/chat.py:395` `send_message_stream` likewise.
- `meeting/graphs/chat_graph/context.py:56` `load_context` grounds on `chat_sess.meeting_id`; the runner already threads `meeting_id` into `state["meeting_id"]` (`runner.py:67`).
- Kickoff (`meeting/services/kickoff.py`, `chat.py:251`) is role-based via `user.role`, never touches a project binding — already satisfies the "role-based, project-agnostic" decision. No change.
- Alembic head in repo = `0021`. New migration `down_revision = "0021"`.

---

# Phase 1 — Backend (TDD)

## Task 1: Migration `0022` — ensure `chat_sessions.meeting_id` is nullable (guarded)

**Files:**
- Create: `alembic/versions/0022_chat_sessions_meeting_nullable.py`
- Test: `tests/meeting/test_migration_0022.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/meeting/test_migration_0022.py
"""0022 — ensure chat_sessions.meeting_id is nullable (user-scoped sessions).

The shared DB is unavailable offline, so this verifies the migration's identity
(single-head chain onto 0021) and that the ORM model declares meeting_id
nullable — the invariant the migration guarantees on the drifted prod DB.
"""
from __future__ import annotations

import importlib

from meeting.db.models import ChatSession


def test_migration_0022_chains_onto_0021():
    mod = importlib.import_module(
        "alembic.versions.0022_chat_sessions_meeting_nullable"
    )
    assert mod.revision == "0022"
    assert mod.down_revision == "0021"


def test_chat_session_meeting_id_is_nullable():
    # The model is the source of truth the migration enforces in the DB.
    assert ChatSession.__table__.c.meeting_id.nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting/test_migration_0022.py -q`
Expected: FAIL — `ModuleNotFoundError` for `alembic.versions.0022_chat_sessions_meeting_nullable` (the second test passes already, since the model is nullable).

- [ ] **Step 3: Write the migration**

```python
# alembic/versions/0022_chat_sessions_meeting_nullable.py
"""chat_sessions.meeting_id nullable — user-scoped chat sessions (decoupled from meeting)

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-15

Sessions become user-scoped: a session no longer belongs to one project. The
meeting_id column was created without NOT NULL in 0002, so on a clean DB this is
a no-op. Guarded/idempotent (like 0021) for the drifted prod DB: only alters when
the column is actually NOT NULL. Existing rows keep their (now-ignored) binding.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("chat_sessions"):
        return
    col = {c["name"]: c for c in insp.get_columns("chat_sessions")}.get("meeting_id")
    if col is not None and not col["nullable"]:
        op.alter_column(
            "chat_sessions",
            "meeting_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )


def downgrade() -> None:
    # No-op: re-adding NOT NULL would fail against user-scoped rows whose
    # meeting_id is intentionally NULL. Decoupling is not reversed.
    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting/test_migration_0022.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0022_chat_sessions_meeting_nullable.py tests/meeting/test_migration_0022.py
git commit -m "feat(db): migration 0022 — guarded chat_sessions.meeting_id nullable (user-scoped sessions)"
```

---

## Task 2: Repo — `delete_chat_session` (hard delete: messages + pending actions + row)

**Files:**
- Modify: `meeting/db/repositories.py` (add after `clear_chat_session`, ~line 699)
- Test: `tests/meeting/test_delete_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/meeting/test_delete_session.py
"""Hard-delete a chat session — repo + endpoint.

Offline (no live DB): a recording fake session captures issued statements, like
test_clear_session. Delete differs from clear by ALSO removing the session row.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import Delete

from meeting.api import chat as chat_api
from meeting.db import repositories as repo

SID = "44444444-4444-4444-4444-444444444444"


class _RecordingSession:
    def __init__(self):
        self.executed: list = []
        self.flushed = False

    async def execute(self, stmt):
        self.executed.append(stmt)
        return None

    async def flush(self):
        self.flushed = True


async def test_delete_chat_session_deletes_messages_pending_and_row():
    sid = uuid.UUID(SID)
    sess = _RecordingSession()

    await repo.delete_chat_session(sess, sid)

    assert all(isinstance(s, Delete) for s in sess.executed)
    tables = {s.table.name for s in sess.executed}
    assert tables == {"chat_messages", "pending_actions", "chat_sessions"}
    # Every DELETE is scoped to this session id.
    for stmt in sess.executed:
        assert sid in stmt.compile().params.values()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `... venv/bin/pytest tests/meeting/test_delete_session.py::test_delete_chat_session_deletes_messages_pending_and_row -q`
Expected: FAIL — `AttributeError: module 'meeting.db.repositories' has no attribute 'delete_chat_session'`.

- [ ] **Step 3: Add the repo function**

Add directly below `clear_chat_session` in `meeting/db/repositories.py`:

```python
async def delete_chat_session(
    session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Hard-delete a chat session: its messages, pending actions, AND the
    session row itself. Distinct from clear_chat_session, which keeps the row.
    The LangGraph checkpoint thread is purged by the API layer (it owns the
    checkpointer handle), mirroring clear_session."""
    await session.execute(
        delete(ChatMessage).where(ChatMessage.session_id == session_id)
    )
    await session.execute(
        delete(PendingAction).where(PendingAction.session_id == session_id)
    )
    await session.execute(
        delete(ChatSession).where(ChatSession.id == session_id)
    )
    await session.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `... venv/bin/pytest tests/meeting/test_delete_session.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add meeting/db/repositories.py tests/meeting/test_delete_session.py
git commit -m "feat(db): delete_chat_session — hard delete (messages + pending + row)"
```

---

## Task 3: API — `DELETE /sessions/{id}` (hard delete + checkpoint purge, idempotent)

**Files:**
- Modify: `meeting/api/chat.py` (add a `delete_session` route after `clear_session`, ~line 323)
- Test: `tests/meeting/test_delete_session.py` (append endpoint tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/meeting/test_delete_session.py`:

```python
# ─── endpoint: DELETE /sessions/{id} ──────────────────────────────────


async def test_delete_endpoint_hard_deletes_and_purges_checkpoint(monkeypatch):
    sid = uuid.UUID(SID)
    sess = object()
    fake_chat = SimpleNamespace(id=sid, meeting_id=None, title="Dự án Mee")

    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    delete_mock = AsyncMock()
    monkeypatch.setattr(repo, "delete_chat_session", delete_mock)
    adelete = AsyncMock()
    monkeypatch.setattr(
        chat_api, "get_checkpointer", lambda: SimpleNamespace(adelete_thread=adelete)
    )

    out = await chat_api.delete_session(SID, session=sess)

    assert out == {"status": "deleted", "session_id": SID}
    delete_mock.assert_awaited_once_with(sess, sid)
    adelete.assert_awaited_once_with(SID)  # thread_id == str(session_id)


async def test_delete_endpoint_404_when_session_missing(monkeypatch):
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=None))
    delete_mock = AsyncMock()
    monkeypatch.setattr(repo, "delete_chat_session", delete_mock)

    with pytest.raises(HTTPException) as ei:
        await chat_api.delete_session(SID, session=object())

    assert ei.value.status_code == 404
    delete_mock.assert_not_awaited()


async def test_delete_endpoint_checkpoint_failure_is_nonfatal(monkeypatch):
    sid = uuid.UUID(SID)
    fake_chat = SimpleNamespace(id=sid, meeting_id=None, title=None)
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    monkeypatch.setattr(repo, "delete_chat_session", AsyncMock())
    boom = AsyncMock(side_effect=RuntimeError("no checkpointer"))
    monkeypatch.setattr(
        chat_api, "get_checkpointer", lambda: SimpleNamespace(adelete_thread=boom)
    )

    out = await chat_api.delete_session(SID, session=object())

    assert out["status"] == "deleted"  # purge failure logged, not raised
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `... venv/bin/pytest tests/meeting/test_delete_session.py -q`
Expected: FAIL — `AttributeError: module 'meeting.api.chat' has no attribute 'delete_session'`.

- [ ] **Step 3: Add the endpoint**

Insert in `meeting/api/chat.py` immediately after the `clear_session` function (after line 322), inside the `# ─── Sessions ───` block:

```python
@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, session: AsyncSession = Depends(get_session)
):
    """Remove a chat session permanently (user-scoped sidebar remove): hard-delete
    its messages + pending actions + the session row, and purge the LangGraph
    checkpoint thread. Distinct from clear (which keeps the row). 404 if missing;
    the checkpoint purge is best-effort and never 500s the delete."""
    sid = _parse_uuid(session_id)
    chat = await repo.get_chat_session(session, sid)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    await repo.delete_chat_session(session, sid)

    try:
        await get_checkpointer().adelete_thread(str(sid))
    except Exception:
        logger.warning("delete: checkpoint purge failed for %s", sid, exc_info=True)

    return {"status": "deleted", "session_id": str(sid)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `... venv/bin/pytest tests/meeting/test_delete_session.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add meeting/api/chat.py tests/meeting/test_delete_session.py
git commit -m "feat(api): DELETE /chat/sessions/{id} — hard delete + checkpoint purge"
```

---

## Task 4: API — per-turn `meeting_id` in send + stream endpoints

**Files:**
- Modify: `meeting/api/chat.py` — `MessageSend` schema (line 54), `send_message` (line 371), `send_message_stream` (line 441)
- Test: `tests/meeting/test_send_meeting_id.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/meeting/test_send_meeting_id.py
"""Send endpoint threads the per-turn (UI-selected) meeting_id into the graph,
instead of grounding on the session's stored binding. None → general (no project)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from meeting.api import chat as chat_api
from meeting.db import repositories as repo

SID = "55555555-5555-5555-5555-555555555555"
TURN_MEETING = "66666666-6666-6666-6666-666666666666"


def _fake_user():
    # ms_oid None → _graph_token_or_401 returns None (no Microsoft path).
    return SimpleNamespace(id=uuid.uuid4(), ms_oid=None)


async def test_send_passes_request_meeting_id_to_graph(monkeypatch):
    sid = uuid.UUID(SID)
    # The session's OWN binding is a DIFFERENT (legacy, ignored) meeting.
    fake_chat = SimpleNamespace(id=sid, meeting_id=uuid.uuid4(), title=None)
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    monkeypatch.setattr(chat_api, "get_checkpointer", lambda: object())

    captured = {}

    async def fake_run_chat_turn(**kwargs):
        captured.update(kwargs)
        return {"status": "complete", "reply": "ok", "intent": None}

    monkeypatch.setattr(chat_api, "run_chat_turn", fake_run_chat_turn)

    req = chat_api.MessageSend(text="hi", meeting_id=TURN_MEETING)
    out = await chat_api.send_message(SID, req, session=object(), user=_fake_user())

    assert out["status"] == "complete"
    assert captured["meeting_id"] == TURN_MEETING  # the UI selection, not the binding


async def test_send_with_no_meeting_id_grounds_generally(monkeypatch):
    sid = uuid.UUID(SID)
    fake_chat = SimpleNamespace(id=sid, meeting_id=uuid.uuid4(), title=None)
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    monkeypatch.setattr(chat_api, "get_checkpointer", lambda: object())

    captured = {}

    async def fake_run_chat_turn(**kwargs):
        captured.update(kwargs)
        return {"status": "complete", "reply": "ok", "intent": None}

    monkeypatch.setattr(chat_api, "run_chat_turn", fake_run_chat_turn)

    req = chat_api.MessageSend(text="hi")  # no project selected
    out = await chat_api.send_message(SID, req, session=object(), user=_fake_user())

    assert out["status"] == "complete"
    assert captured["meeting_id"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `... venv/bin/pytest tests/meeting/test_send_meeting_id.py -q`
Expected: FAIL — `pydantic ... ValidationError` / unexpected kwarg `meeting_id` on `MessageSend`, or the assertion fails because `captured["meeting_id"]` equals the session binding.

- [ ] **Step 3: Add `meeting_id` to the schema and thread it through**

In `meeting/api/chat.py`, change `MessageSend` (line 54):

```python
class MessageSend(BaseModel):
    text: str
    # The UI-selected project for THIS turn (user-scoped sessions: grounding is
    # per-turn, not bound to the session). None → answer without project grounding.
    meeting_id: Optional[str] = None
```

In `send_message`, replace the `run_chat_turn(...)` call's `meeting_id` argument (line 371) — change:

```python
        meeting_id=str(chat.meeting_id) if chat.meeting_id else None,
```
to:
```python
        meeting_id=req.meeting_id,
```

In `send_message_stream`, the request body must carry `meeting_id`. The `req: MessageSend` parameter is already in scope and captured by the `gen()` closure. Replace the `stream_chat_turn(...)` call's `meeting_id` argument (line 441) — change:

```python
                    meeting_id=str(chat.meeting_id) if chat.meeting_id else None,
```
to:
```python
                    meeting_id=req.meeting_id,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `... venv/bin/pytest tests/meeting/test_send_meeting_id.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add meeting/api/chat.py tests/meeting/test_send_meeting_id.py
git commit -m "feat(api): per-turn meeting_id on send/stream (decouple grounding from session)"
```

---

## Task 5: Grounding — `load_context` grounds on the per-turn `state["meeting_id"]`

**Files:**
- Modify: `meeting/graphs/chat_graph/context.py` — `load_context` (lines 56–131)
- Test: `tests/meeting/test_chat_project_memory.py` (update the two `load_context` tests) + new file `tests/meeting/test_load_context_grounding.py`

- [ ] **Step 1: Write the failing tests (new grounding behavior)**

```python
# tests/meeting/test_load_context_grounding.py
"""load_context grounds on the per-turn meeting_id (UI selection) carried in
state, not on a session column. None → no project grounding (general turn)."""
from __future__ import annotations

import uuid

import pytest

from meeting.graphs.chat_graph import context as ctx


class _Meeting:
    def __init__(self, mid):
        self.id = mid
        self.title = "AI Innovation Project"
        self.project_summary_json = {"narrative": "Đang chạy"}
        self.recordings = []


def _patch(monkeypatch, meeting):
    async def fake_list_chat_messages(session, sid, limit=10):
        return []

    async def fake_get_meeting(session, mid):
        # Return the meeting only when asked for the per-turn id.
        return meeting if str(mid) == str(meeting.id) else None

    monkeypatch.setattr(ctx.repo, "list_chat_messages", fake_list_chat_messages)
    monkeypatch.setattr(ctx.repo, "get_meeting", fake_get_meeting)


async def test_grounds_on_state_meeting_id(monkeypatch):
    mid = uuid.uuid4()
    _patch(monkeypatch, _Meeting(mid))
    load_context = ctx.make_load_context(
        session=None, search_record=lambda pid: None, schedule_resync=lambda m: None
    )

    out = await load_context(
        {"session_id": str(uuid.uuid4()), "meeting_id": str(mid)}
    )

    assert out["meeting_context"]["id"] == str(mid)
    assert out["meeting_context"]["title"] == "AI Innovation Project"
    assert out["resolved_meeting_id"] == str(mid)


async def test_no_project_grounding_when_state_meeting_id_none(monkeypatch):
    _patch(monkeypatch, _Meeting(uuid.uuid4()))
    load_context = ctx.make_load_context(
        session=None, search_record=lambda pid: None, schedule_resync=lambda m: None
    )

    out = await load_context({"session_id": str(uuid.uuid4())})  # no meeting_id

    assert out["meeting_context"] == {}
    assert out["project_memory"] == ""
    assert out["resolved_meeting_id"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `... venv/bin/pytest tests/meeting/test_load_context_grounding.py -q`
Expected: FAIL — `test_grounds_on_state_meeting_id` fails because `load_context` still reads `chat_sess.meeting_id` (calls `repo.get_chat_session`, which is unpatched here) and never grounds on `state["meeting_id"]`.

- [ ] **Step 3: Change the grounding source in `load_context`**

In `meeting/graphs/chat_graph/context.py`, inside `load_context`, replace the session-binding lookup (lines 63–67):

```python
        meeting_ctx = {}
        project_memory = ""
        chat_sess = await repo.get_chat_session(session, sid)
        if chat_sess and chat_sess.meeting_id:
            meeting = await repo.get_meeting(session, chat_sess.meeting_id)
            if meeting:
```

with the per-turn grounding source:

```python
        meeting_ctx = {}
        project_memory = ""
        # User-scoped sessions: ground on the per-turn meeting_id (the UI-selected
        # project passed with this message), NOT a session column. None → general.
        turn_meeting_id = state.get("meeting_id")
        if turn_meeting_id:
            meeting = await repo.get_meeting(session, uuid.UUID(turn_meeting_id))
            if meeting:
```

(Everything inside that `if meeting:` block — the AgentBase recall, staleness check, and `meeting_ctx` assignment — is unchanged. The final `return` already uses `meeting_ctx.get("id") or state.get("meeting_id")`, which now resolves correctly.)

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `... venv/bin/pytest tests/meeting/test_load_context_grounding.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Update the two existing `load_context` tests in `test_chat_project_memory.py`**

The two staleness tests pass state with no `meeting_id` and stub `get_chat_session`. Under the new design `load_context` no longer reads the session binding, so they must pass the meeting id in state. In `tests/meeting/test_chat_project_memory.py`:

Replace `_patch_repo` (lines 110–123) — remove the now-unused `get_chat_session` stub:

```python
def _patch_repo(monkeypatch, meeting):
    """Stub the repo calls load_context makes so it runs without a DB."""
    async def fake_list_chat_messages(session, sid, limit=10):
        return []

    async def fake_get_meeting(session, mid):
        return meeting

    monkeypatch.setattr(ctx.repo, "list_chat_messages", fake_list_chat_messages)
    monkeypatch.setattr(ctx.repo, "get_meeting", fake_get_meeting)
```

Delete the now-unused `_ChatSess` class (lines 105–107).

In `test_load_context_flags_stale_record_and_kicks_resync`, change the call (line 152):

```python
    out = await load_context(
        {"session_id": str(uuid.uuid4()), "meeting_id": str(meeting.id)}
    )
```

In `test_load_context_no_note_when_record_is_fresh`, change the call (line 180):

```python
    out = await load_context(
        {"session_id": str(uuid.uuid4()), "meeting_id": str(meeting.id)}
    )
```

- [ ] **Step 6: Run the grounding + memory suite to verify green**

Run: `... venv/bin/pytest tests/meeting/test_chat_project_memory.py tests/meeting/test_load_context_grounding.py -q`
Expected: PASS (all).

- [ ] **Step 7: Run the FULL suite to confirm no regressions**

Run: `DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting -q`
Expected: PASS — baseline 313 + new tests (≈ 322). If any other test grounded via `chat_sess.meeting_id`, fix it to pass `meeting_id` in state (same edit as Step 5).

- [ ] **Step 8: Commit**

```bash
git add meeting/graphs/chat_graph/context.py tests/meeting/test_chat_project_memory.py tests/meeting/test_load_context_grounding.py
git commit -m "feat(chat): load_context grounds on per-turn meeting_id (user-scoped sessions)"
```

---

# Phase 2 — Frontend (implementation + manual verification)

> No FE test suite exists. Each FE task ends with `npm run build` (tsc typecheck + Vite build) green, then a manual verification note. Run from `meeting_frontend_react/`.

## Task 6: API client — user-scoped session methods + per-turn `meeting_id`

**Files:**
- Modify: `meeting_frontend_react/src/api/client.ts` (chat block, lines 372–475)
- Modify: `meeting_frontend_react/src/types/api.ts` (add a `ChatSessionSummary` type)

- [ ] **Step 1: Add the `ChatSessionSummary` type**

In `meeting_frontend_react/src/types/api.ts`, add:

```typescript
export interface ChatSessionSummary {
  id: string;
  meeting_id: string | null;
  title: string | null;
  created_at: string;
  last_activity_at: string;
}
```

- [ ] **Step 2: Update the chat client block**

In `meeting_frontend_react/src/api/client.ts`, import `ChatSessionSummary` with the other type imports. Replace `createSession`, `send`, `sendStream`, and add `listSessions`, `remove`, `sessionDetail` (keep `kickoff`, `approve`, `reject`, `clear` as-is):

```typescript
    // Create a user-scoped session. No meeting binding — grounding is per-turn.
    createSession: (title?: string) =>
      http<{ id: string; meeting_id: string | null; title: string; created_at: string }>(
        "POST", "/api/chat/sessions", title ? { title } : {},
      ),
    // List the user's sessions (sidebar), most-recently-active first.
    listSessions: () =>
      http<ChatSessionSummary[]>("GET", "/api/chat/sessions"),
    // Session detail + messages (used when switching sessions in the sidebar).
    sessionDetail: (sessionId: string) =>
      http<{
        id: string;
        meeting_id: string | null;
        title: string | null;
        messages: Array<{
          id: string;
          role: string;
          content: { text?: string };
          created_at: string;
        }>;
      }>("GET", `/api/chat/sessions/${sessionId}`),
    // Remove a session permanently (hard delete + checkpoint purge).
    remove: (sessionId: string) =>
      http<{ status: string; session_id: string }>(
        "DELETE", `/api/chat/sessions/${sessionId}`,
      ),
```

Update `send` to carry the per-turn project:

```typescript
    send: (sessionId: string, text: string, meetingId: string | null) =>
      http<ChatTurnResult>(
        "POST", `/api/chat/sessions/${sessionId}/messages`,
        { text, meeting_id: meetingId },
      ),
```

Update `sendStream`'s signature and request body (only the signature line and the `body:` line change; the SSE parsing loop is unchanged):

```typescript
    sendStream: async (
      sessionId: string,
      text: string,
      meetingId: string | null,
      onStep: (ev: ChatStreamStep) => void,
      signal?: AbortSignal,
    ): Promise<ChatTurnResult> => {
      const r = await fetch(`/api/chat/sessions/${sessionId}/messages/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, meeting_id: meetingId }),
        signal,
      });
      // ...rest of the function body unchanged
```

Confirm `http` accepts `"DELETE"`. If its method-type union is restricted (e.g. `"GET" | "POST"`), add `"DELETE"` to it.

- [ ] **Step 3: Verify the build (call-site break in ChatPane is fixed in Task 7)**

Run: `cd meeting_frontend_react && npm run build`
Expected: TypeScript errors ONLY in `ChatPane.tsx` (the `createSession`/`send`/`sendStream` call sites have new signatures) — fixed in Task 7. No errors in `client.ts` / `types/api.ts`.

- [ ] **Step 4: Commit**

```bash
git add meeting_frontend_react/src/api/client.ts meeting_frontend_react/src/types/api.ts
git commit -m "feat(fe): chat client — listSessions/sessionDetail/remove + per-turn meeting_id"
```

---

## Task 7: ChatPane — user-scoped sessions (sidebar list, new session, remove, per-turn project)

**Files:**
- Modify: `meeting_frontend_react/src/components/ChatPane.tsx`

This replaces the per-meeting session model (keyed by `mee.chat.${currentMeetingId}`) with a single user-scoped session list. The selected project (`currentMeetingId`) becomes per-turn grounding only — it no longer creates or switches sessions.

- [ ] **Step 1: Replace the type import + session lifecycle state**

Change the type import (line 12) to add `ChatSessionSummary`:

```typescript
import type { ChatSessionSummary, ChatStreamStep, ChatTurnResult, PendingAction } from "../types/api";
```

Remove the per-meeting refs and `localStorage` logic (lines 56–134: `sessionIdRef`, `sessionMeetingRef`, `kickedOffRef`, `storageKey`, the restore `useEffect`, and the save `useEffect`). Replace with user-scoped state:

```typescript
  // User-scoped sessions: the sidebar list + the active session. Sessions are
  // decoupled from projects — currentMeetingId is sent per-turn for grounding.
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  useEffect(() => { activeSessionIdRef.current = activeSessionId; }, [activeSessionId]);
  // Session ids already kicked off this mount, so Mee greets an empty thread once.
  const kickedOffRef = useRef<Set<string>>(new Set());
```

- [ ] **Step 2: Add the session helpers (`maybeKickoff`, `openSession`, `createAndOpenSession`)**

Place these after `pushAgent`/`pushNote`/`applyResult` are defined (so they can call `pushAgent`/`errorText`), before `ensureSession`:

```typescript
  const maybeKickoff = useCallback(async (sid: string) => {
    if (kickedOffRef.current.has(sid)) return;
    kickedOffRef.current.add(sid);
    setBusy(true);
    try {
      const res = await api.chat.kickoff(sid);
      if (res.reply) setMessages((m) => [...m, { role: "agent", text: res.reply as string }]);
    } catch {
      /* best-effort — WelcomeBanner remains the fallback */
    } finally {
      setBusy(false);
    }
  }, []);

  // Load a session's messages into the thread and switch to it. Fires kickoff
  // if the thread is empty (role-based, project-agnostic greeting).
  const openSession = useCallback(async (sid: string) => {
    setActiveSessionId(sid);
    setPending(null);
    try {
      const detail = await api.chat.sessionDetail(sid);
      const msgs: ThreadMsg[] = (detail.messages ?? []).map((m) => ({
        role: m.role === "user" ? "user" : "agent",
        text: typeof m.content?.text === "string" ? m.content.text : "",
      }));
      setMessages(msgs);
      if (msgs.length === 0) await maybeKickoff(sid);
    } catch {
      setMessages([]);
    }
  }, [maybeKickoff]);

  // Create a fresh user-scoped session, prepend it to the sidebar, switch to it,
  // and kick off the greeting.
  const createAndOpenSession = useCallback(async () => {
    const s = await api.chat.createSession();
    setSessions((prev) => [
      { id: s.id, meeting_id: s.meeting_id, title: s.title, created_at: s.created_at, last_activity_at: s.created_at },
      ...prev,
    ]);
    setActiveSessionId(s.id);
    setMessages([]);
    setPending(null);
    await maybeKickoff(s.id);
  }, [maybeKickoff]);
```

- [ ] **Step 3: Load sessions on mount; open the most-recent (or create one)**

```typescript
  // On mount: fetch the user's sessions and open the most-recently-active. The
  // backend returns them ordered last_activity_at desc, so [0] is the target.
  useEffect(() => {
    void (async () => {
      try {
        const list = await api.chat.listSessions();
        setSessions(list);
        if (list.length > 0) await openSession(list[0].id);
        else await createAndOpenSession();
      } catch {
        /* best-effort — an empty pane with the New-session button remains usable */
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
```

- [ ] **Step 4: Rewrite `ensureSession` for the active session**

Replace the existing `ensureSession` (lines 207–215):

```typescript
  const ensureSession = useCallback(async (): Promise<string> => {
    if (activeSessionIdRef.current) return activeSessionIdRef.current;
    const s = await api.chat.createSession();
    setSessions((prev) => [
      { id: s.id, meeting_id: s.meeting_id, title: s.title, created_at: s.created_at, last_activity_at: s.created_at },
      ...prev,
    ]);
    setActiveSessionId(s.id);
    return s.id;
  }, []);
```

- [ ] **Step 5: Pass `currentMeetingId` as the per-turn project in `handleSend`**

In `handleSend`, change the two transport calls (lines 242 + 247):

```typescript
        res = await api.chat.sendStream(sid, text, currentMeetingId, onStep, ctrl.signal);
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") throw e;
        if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
          res = await api.chat.send(sid, text, currentMeetingId);
        } else {
          throw e;
        }
      }
```

Add `currentMeetingId` to the `handleSend` `useCallback` dependency array (line 264).

- [ ] **Step 6: Point `handleClear` at the active session**

In `handleClear`, replace `const sid = sessionIdRef.current;` (line 280) with:

```typescript
    const sid = activeSessionIdRef.current;
```

- [ ] **Step 7: Add `handleNewSession` and `handleRemoveSession`**

```typescript
  const handleNewSession = useCallback(async () => {
    if (busy) return;
    try {
      await createAndOpenSession();
    } catch (e) {
      pushAgent(errorText(e));
    }
  }, [busy, createAndOpenSession]);

  const handleRemoveSession = useCallback(
    async (sid: string) => {
      const ok = await confirm({
        title: t("chat.session.remove"),
        message: t("chat.session.removeConfirm"),
        confirmLabel: t("chat.session.remove"),
        cancelLabel: t("confirm.cancel"),
        accent: true,
      });
      if (!ok) return;
      try {
        await api.chat.remove(sid);
        const rest = sessions.filter((s) => s.id !== sid);
        setSessions(rest);
        kickedOffRef.current.delete(sid);
        if (activeSessionIdRef.current === sid) {
          if (rest.length > 0) await openSession(rest[0].id);
          else await createAndOpenSession();
        }
      } catch (e) {
        pushAgent(errorText(e));
      }
    },
    [sessions, confirm, t, openSession, createAndOpenSession],
  );
```

- [ ] **Step 8: Render the New-session button + sidebar list**

In the `pane-meta` block (lines 479–498), add a "New session" button before the Clear button:

```tsx
          <button
            className="icon-btn icon-btn-sm"
            type="button"
            title={t("chat.session.new")}
            aria-label={t("chat.session.new")}
            disabled={busy}
            onClick={() => void handleNewSession()}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
```

Insert the sidebar list immediately before `<div className="chat-thread" ref={threadRef}>` (line 501):

```tsx
      {sessions.length > 0 && (
        <ul className="chat-session-list" aria-label={t("chat.session.listLabel")}>
          {sessions.map((s) => (
            <li
              key={s.id}
              className={`chat-session-item${s.id === activeSessionId ? " is-active" : ""}`}
            >
              <button
                type="button"
                className="chat-session-open"
                onClick={() => void openSession(s.id)}
                disabled={busy}
              >
                {s.title || t("chat.session.untitled")}
              </button>
              <button
                type="button"
                className="chat-session-remove"
                title={t("chat.session.remove")}
                aria-label={t("chat.session.remove")}
                onClick={() => void handleRemoveSession(s.id)}
                disabled={busy}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
```

- [ ] **Step 9: Typecheck + build**

Run: `cd meeting_frontend_react && npm run build`
Expected: PASS (no TS errors). Fix any remaining call-site/type mismatches (e.g. `currentMeetingId` is already destructured from `useApp()` at line 34).

- [ ] **Step 10: Commit**

```bash
git add meeting_frontend_react/src/components/ChatPane.tsx
git commit -m "feat(fe): user-scoped chat sessions — sidebar list, new session, remove, per-turn project"
```

---

## Task 8: i18n strings (VI + EN) + minimal sidebar styling

**Files:**
- Modify: `meeting_frontend_react/src/i18n.ts`
- Modify: the app stylesheet (find with `grep -rl "chat-thread" meeting_frontend_react/src --include=*.css`) — add `.chat-session-list` rules.

- [ ] **Step 1: Add the new keys to both locales**

Add to the VI map and the EN map (match the existing flat-key structure in `i18n.ts`). VI:

```typescript
    "chat.session.new": "Phiên mới",
    "chat.session.remove": "Xóa phiên",
    "chat.session.removeConfirm": "Xóa vĩnh viễn phiên trò chuyện này? Không thể hoàn tác.",
    "chat.session.listLabel": "Danh sách phiên",
    "chat.session.untitled": "Phiên không tên",
```

EN:

```typescript
    "chat.session.new": "New session",
    "chat.session.remove": "Remove session",
    "chat.session.removeConfirm": "Permanently delete this chat session? This cannot be undone.",
    "chat.session.listLabel": "Session list",
    "chat.session.untitled": "Untitled session",
```

- [ ] **Step 2: Add minimal sidebar styling**

In the stylesheet that defines `.chat-thread`, add:

```css
.chat-session-list { display: flex; gap: 6px; overflow-x: auto; padding: 6px 10px; margin: 0; list-style: none; border-bottom: 1px solid var(--border, #e5e7eb); }
.chat-session-item { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; background: var(--chip-bg, #f3f4f6); white-space: nowrap; font-size: 12px; }
.chat-session-item.is-active { background: var(--accent-soft, #dbeafe); }
.chat-session-open { border: 0; background: none; cursor: pointer; max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
.chat-session-remove { border: 0; background: none; cursor: pointer; opacity: 0.6; }
.chat-session-remove:hover { opacity: 1; }
```

(Use the project's actual CSS variables if they differ; the fallbacks keep it functional regardless.)

- [ ] **Step 3: Typecheck + build**

Run: `cd meeting_frontend_react && npm run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add meeting_frontend_react/src/i18n.ts meeting_frontend_react/src/*.css
git commit -m "feat(fe): i18n + styling for user-scoped chat sessions"
```

---

## Task 9: Manual verification (end-to-end)

**Files:** none (verification only).

- [ ] **Step 1: Apply the migration (user runs this)**

`venv/bin/alembic upgrade head` — on a clean DB this is a no-op; on prod it ensures nullability. The shared prod DB has Alembic drift — confirm with the user before running.

- [ ] **Step 2: Run backend + frontend**

```bash
venv/bin/python run_meeting.py          # HTTP :8002 + WS :9091
cd meeting_frontend_react && npm run dev # :8001
```

- [ ] **Step 3: Verify the flows**

- Open the app → a session opens automatically; if none exist, one is created and Mee kicks off with the role-based greeting (no project selected).
- Click "New session" → fresh empty thread, Mee greets again; the old session stays in the sidebar.
- Select a project, send a message → reply grounded on that project. Switch project, send again → re-grounded. Deselect project (general) → answers without project grounding, no error.
- Switch sessions via the sidebar → each restores its own messages.
- Remove (✕) a session → confirm → it disappears; if it was active, the most-recent remaining session opens (or a fresh one is created). Reload → it does not reappear (hard delete).
- Reload the page → the most-recently-active session opens.

- [ ] **Step 4: Final full backend suite**

Run: `DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting -q`
Expected: PASS (≈ 322).

---

## Self-Review (completed during planning)

- **Spec coverage:** A (migration `0022` Task 1; `last_activity_at` already exists — confirmed); B (`delete_chat_session` Task 2; `create`/`list`-ordering/`add_chat_message`-bump already implemented — confirmed in Pre-flight); C (create no-meeting already works; `GET /sessions` already returns `last_activity_at`; `DELETE` Task 3; per-turn `meeting_id` on send/stream Task 4; kickoff already project-agnostic — no change); D (grounding Task 5); E (client Task 6, ChatPane Task 7, i18n+CSS Task 8). No-project kickoff = role-based/project-agnostic, already the existing behavior. ✅
- **Placeholder scan:** every code step shows full code; no TBD / "handle edge cases". ✅
- **Type consistency:** `delete_chat_session(session, session_id)`; `delete_session` endpoint → `{"status":"deleted",...}`; `MessageSend.meeting_id`; `ChatSessionSummary`; client `createSession()` / `listSessions()` / `sessionDetail()` / `remove()` / `send(.., meetingId)` / `sendStream(.., meetingId, onStep, signal)` — names consistent across tasks. ✅
- **Executor note:** if the full suite (Task 5 Step 7) surfaces any other test that built `load_context` state without `meeting_id` yet expected project grounding, apply the same fix as Task 5 Step 5 (pass `meeting_id` in state).
