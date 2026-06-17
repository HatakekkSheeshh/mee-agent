# Chat UX upgrade — streaming activity trace + HITL card & polish (design)

**Date:** 2026-06-10 · **Branch:** `feat/mail-tool` · **Status:** approved (user: "all your rcm is good, make it in this branch")

## Problem

Sending a chat message is a single blocking POST: the FE sets a `busy` flag and waits for the
WHOLE graph (classify → multi-round agent loop → tools → reply), often 10–60 s of dead air.
The user can neither see progress nor cancel. Side-effect tools other than `create_task`
fall back to a raw-JSON approval card (bad fit for the upcoming `send_email` /
`schedule_meeting` Graph tools). Two small known issues: the `.mee-modal-backdrop`
`backdrop-filter: blur(3px)` jank (diagnosed in HANDOFF), and no visual mark for rejected actions.

## Scope (4 phases, one branch)

### P0 — quick fixes
- Remove `backdrop-filter: blur(3px)` (+`-webkit-`) from `.mee-modal-backdrop`
  (`styles-legacy.css:2104`); keep the rgba dim.
- Stop button: while busy the send button becomes a stop control; aborts the in-flight
  request via `AbortController` (client-side abort; backend turn may still finish server-side —
  the FE drops the response and appends a "(đã dừng)" note).

### P1 — SSE streaming + activity trace
**Backend** (no graph/node changes — replay-safety untouched):
- `runner.py`: `stream_chat_turn(...)` async generator. Internally
  `graph.astream(initial_state, config, stream_mode="updates")`; each `{node: delta}` chunk
  maps to step events via a PURE helper `update_to_events(node, delta)` (unit-testable):
  - `load_context` → `{type:"step", step:"context"}`
  - `classify_intent` → `{type:"step", step:"classify", intent}`
  - `agent` with `agent_route=="tools"` → `{type:"step", step:"tool_call", tools:[names]}`
    (names from the last `agent_messages` entry's `tool_calls`)
  - `agent_tools` → `{type:"step", step:"tool_done"}`
  - `pm_call`/`pm_await` → `{type:"step", step:"pm"}`
  - other nodes → no event
  After the stream ends, reuse `_interrupt_or_complete` (post-invoke snapshot) and yield a
  final `{type:"result", result}` marker.
- `api/chat.py`: `POST /sessions/{id}/messages/stream` → `StreamingResponse`
  (`text/event-stream`, `data: <json>\n\n` frames). The generator opens its OWN
  `AsyncSessionLocal()` (a `Depends(get_session)` session is torn down before a
  StreamingResponse body runs). Terminal frame: on interrupt → `_persist_interrupt` then
  `{type:"interrupted", ...}`; else `{type:"complete", reply, intent, tool_result}`;
  exceptions → `{type:"error", detail}`. The old blocking endpoint stays (compat + tests).

**Frontend:**
- `client.ts` `chat.sendStream(sessionId, text, onStep, signal)` — `fetch` + ReadableStream
  SSE line parser; resolves with the terminal event mapped to `ChatTurnResult`.
- `ChatPane`: live trace while busy (step lines, i18n labels `chat.step.*` + `tool.*` name map,
  spinner on the latest); on completion the trace collapses into the agent message
  (`ThreadMsg.steps?: string[]` rendered as `<details>`), so it persists via localStorage.
  Falls back to the blocking `api.chat.send` if the stream endpoint errors with 404/405.

### P2 — generic editable action card
`ActionArgsCard.tsx` for pending actions that are NOT create_task and NOT pm-kinds: each
string arg renders as an editable field (textarea for `body`/`description`/long values, input
otherwise); non-string args render read-only JSON. Approve sends `{edited_args}` — the backend
already merges `edited_args` for generic tools (`agent.py` `agent_execute`). Replaces the raw
`<pre>` JSON card; `send_email` (to/subject/body) and the future `schedule_meeting` get a real
editing UX with zero further FE work.

### P3 — polish
- Copy button on agent messages (clipboard, transient "copied" state).
- Reject mark: on reject the FE appends a dimmed note line `✕ <tool>` (`role:"note"`) before
  the canned reject reply.
- (Suggestion chips already exist — no work.)

## Testing
- New `tests/meeting/test_stream_events.py`: pure-function tests for `update_to_events`
  (each node mapping + unknown-node → []), per the repo's no-DB convention.
- Existing suite must stay green (`ECC_GATEGUARD=off venv/bin/python -m pytest tests/meeting -q`,
  92 passing) and `npm run build` clean.
- Live SSE smoke deferred to the next live-backend session (same blocker as everything else).

## Non-goals
- Token-level streaming of the final answer (needs the agent node's LLM call to stream;
  deferred — step events already remove most dead air).
- Persisting activity traces server-side; trace lives in the FE thread only.
- Changes to graph topology, HITL semantics, or the pm-agent branch.
