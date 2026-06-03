# Design: pm-agent A2A integration in the meeting chat graph

**Date:** 2026-06-02
**Status:** Approved (design phase) — pending implementation plan
**Branch:** `feat/backend-agents`

## Problem

The Mee meeting agent's chat assistant can answer questions about meetings and run a
few local tools, but it cannot perform project-management actions (Redmine issues).
A separate **pm-agent** already does this and exposes an A2A (Agent-to-Agent) endpoint.
We want the meeting chat to drive pm-agent over A2A so a user can, from one chat,
ask things like "tạo issue cho việc deploy v1" or "liệt kê issue overdue của tôi"
and have it executed (with approval) on Redmine.

## Goal

Extend the **existing** chat graph with a new `pm_task` branch that:
1. Routes PM-related chat messages to pm-agent via A2A.
2. Mirrors pm-agent's human-in-the-loop (HITL) approvals back to the chat user.
3. Leaves a clean, **deferred** seam for injecting `transcript_segments` context into
   pm-agent requests (the trigger/shape of that is TBD — owner will specify later).

Non-goals (v1): streaming/SSE consumption, Microsoft OIDC per-user auth, building a
separate bridge agent, exposing this agent's own A2A server.

## Integration surface (pm-agent, verified from `projects/pm-agent`)

- Endpoint: `https://endpoint-e05e38a1-6070-4f9d-8ab2-80ea965ec2f6.agentbase-runtime.aiplatform.vngcloud.vn/a2a/`
- Protocol: A2A v0.3 JSON-RPC. Methods: `message/send`, `message/stream`, `tasks/cancel`
  (server has `enable_v0_3_compat=True`). We use **`message/send`** (non-streaming).
- Auth: `X-API-KEY: <key>` header **or** Microsoft OIDC Bearer JWT. Discovery
  (`/.well-known/agent-card.json`) is public. **We use static `X-API-KEY`.**
- Skills: `redmine_query` (list/search/report issues) and `redmine_mutate`
  (create/update/bulk-update, **HITL**).
- HITL contract: write ops return a task in `INPUT_REQUIRED` with an `approval_request`
  DataPart (`issues`, `message`). Caller resumes the **same `task_id`** with a DataPart
  `{approval_action: approve|edit|reject, approval_input: ...}`. It may also return
  `INPUT_REQUIRED` as `need_more_info` (free-text expected).
- `task_id` ⇄ pm-agent's internal LangGraph `thread_id` is 1-to-1. First `message/send`
  has no task → pm-agent mints one, returned on the Task object; resumes reuse it.

## Architecture

Two layers change (mirrors the existing split documented in CLAUDE.md):

| Layer | File | Change |
|---|---|---|
| A2A client | `meeting/services/pm_agent_client.py` | **NEW** — thin async JSON-RPC client. |
| Agent logic | `meeting/graphs/chat_graph.py` | **EDIT** — new `pm_task` intent + `pm_call`/`pm_await` nodes + looped edges. |
| Package facade | `meeting/graphs/__init__.py` | **EDIT** if any new exported helper is added (keep `run_chat_turn`/`resume_chat_turn` signatures stable). |
| HTTP layer | `meeting/api/chat.py` | **EDIT** — widen resume payload to carry free-text + `approval_action`. |
| Config | `.env.example` | **EDIT** — `PM_AGENT_A2A_URL`, `PM_AGENT_API_KEY` (placeholder). |

### 1. A2A client — `meeting/services/pm_agent_client.py`

A small `httpx` async wrapper, **not** the `a2a-sdk`. Rationale: `a2a-sdk 1.0.2` forces
`protobuf<6` (pm-agent's own requirements.txt documents this conflict); the meeting repo
should not inherit that pin for the sake of two RPC calls. We construct A2A v0.3 JSON-RPC
requests directly.

Normalized result type (framework-agnostic so the graph never sees protobuf/JSON-RPC):

```python
@dataclass(frozen=True)
class PmAgentResult:
    task_id: str
    state: Literal["completed", "failed", "input_required", "working"]
    text: str                      # human-readable message / text artifact
    need_approval: bool            # True iff INPUT_REQUIRED carried approval_request DataPart
    issues: list[dict] | None      # approval payload (issues), if any
```

Public API:

```python
async def send_message(text: str, *, task_id: str | None = None,
                       data_part: dict | None = None) -> PmAgentResult
async def cancel(task_id: str) -> None
```

- Reads `PM_AGENT_A2A_URL` + `PM_AGENT_API_KEY` from env (own per-service config; no shared
  client — consistent with repo convention).
- Sets `X-API-KEY` on every call. Sends `message/send` with the user text part, plus a
  DataPart when `data_part` is provided (approval payload).
- Parses the returned Task: maps `status.state` → our `state`; extracts text artifacts /
  status message → `text`; detects the `approval_request` DataPart → `need_approval` +
  `issues`. Reads back `task.id` → `task_id`.
- Timeout + non-200 + transport error → raise a typed `PmAgentError`; the graph catches it.

### 2. Chat graph extension — `meeting/graphs/chat_graph.py`

New intent `pm_task` alongside `question` / `tool`. New topology (additions in **bold**):

```
load_context → classify_intent ─┬─ question → answer ─────────────────────────┐
                                ├─ tool     → propose_action → execute_action ─┤
                                └─ **pm_task → pm_call**                        │
                                                  │                            │
                          ┌──── input_required ───┘                            │
                          ▼                                                     │
                     **pm_await** ──(interrupt: ask user)──┐                    │
                          ▲           resume w/ decision    │                   │
                          └──────────── **pm_call** ◄───────┘                   │
                                                  │                             │
                              completed / failed ─┘ → **pm_reply** ─────────────┤
                                                                                ▼
                                                                          save_reply → END
```

**Critical correctness constraint:** LangGraph re-runs an interrupted node from its top on
resume, with `interrupt()` returning the resume value the second time. Therefore the
non-idempotent A2A send **must not** sit in the same node as `interrupt()`, or it would
double-send on every resume. We isolate them:

- **`pm_call`** (no `interrupt()`): performs exactly **one** `send_message(...)` using
  `pm_task_id` + the queued `pm_next_payload`. Writes `PmAgentResult` into state.
  Idempotent per node-invocation.
- **`pm_await`** (the only `interrupt()` in this branch): surfaces the pending request
  (need_more_info → text prompt; need_approval → issues card + approve/edit/reject).
  On resume, stores the user's decision into `pm_next_payload` and routes back to `pm_call`.

Conditional edge after `pm_call`:
- `completed` / `failed` → `pm_reply` (formats final text) → `save_reply`.
- `input_required` → `pm_await`.

Loop is bounded by `PM_MAX_ROUNDS` (safety cap, default 6) against a misbehaving agent.

#### State additions (`ChatState`)

| Field | Meaning |
|---|---|
| `pm_task_id: str \| None` | A2A task id; None on first call, set from result. Persisted across interrupts. |
| `pm_next_payload: dict` | What to send next: `{kind:"start", text}` \| `{kind:"text", text}` \| `{kind:"approval", approval_action, approval_input}`. |
| `pm_last: dict \| None` | Last normalized `PmAgentResult` (as dict). |
| `pm_pending: dict \| None` | Payload handed to `interrupt()` for the FE. |
| `pm_rounds: int` | Loop counter for the safety cap. |

All checkpointed (thread_id = session_id), so a multi-step pm-agent conversation survives
across approve/reject round-trips on one chat thread.

### 3. Classification

Extend the `classify_intent` system prompt with the `pm_task` intent and a short summary
of pm-agent's skills (query/report issues; create/update with approval). Output JSON gains
`intent: "question" | "tool" | "pm_task"`. The `route_after_classify` edge gets a `pm_task`
branch to `pm_call`.

### 4. HTTP layer — `meeting/api/chat.py`

The existing resume path only carries `approved`/`rejected` + `edited_args`. pm-agent needs:
- free-text for `need_more_info`,
- an `approval_action` of `approve | edit | reject` for `need_approval`.

Changes:
- Widen `ApprovalRequest` (or add a sibling schema) with optional `approval_action: str`
  and `text: str`.
- When the persisted `PendingAction` represents a pm-agent step (distinguish via
  `tool_name = "pm_agent"` or a metadata flag), the approve/reject endpoints build a
  decision dict carrying `approval_action` / `text` and resume the graph as today.
- `pm_await`'s `interrupt()` payload is persisted as a `PendingAction` (reusing
  `create_pending_action`) with `tool_name="pm_agent"`, `tool_args={issues|prompt}`,
  so the existing `/pending-actions` listing + FE rendering keep working.
- A new FE affordance (text reply box for `need_more_info`) is required, but the API shape
  is additive — existing approve/reject still work for `need_approval`.

### 5. Transcript_segments — DEFERRED seam (explicit)

Owner will specify the use case later. v1 leaves exactly one hook and no more (YAGNI):
`pm_call` may call a repository function to fetch transcript context for the chat's bound
meeting/recording and fold it into the request text sent to pm-agent. The **trigger**
(which meeting/recording, how much text, when to include it) is **TBD** and intentionally
not designed here. No schema or prompt changes are made for it in v1.

### 6. Config (`.env.example`)

```env
# ─── pm-agent (A2A) ───────────────────────────────────────
PM_AGENT_A2A_URL=https://endpoint-e05e38a1-6070-4f9d-8ab2-80ea965ec2f6.agentbase-runtime.aiplatform.vngcloud.vn/a2a/
PM_AGENT_API_KEY=your-pm-agent-api-key-here   # pm-agent's API_SEC_KEY (X-API-KEY)
```

The real key is supplied at deploy time in `.env` (gitignored). No secret is committed.

### 7. Error handling

- A2A transport error / timeout / non-200 / `state="failed"` → friendly Vietnamese reply,
  `tool_result.error` set, graph ends cleanly (no crash).
- Reject decision → optionally call `client.cancel(pm_task_id)` so pm-agent frees the task.
- Loop exceeds `PM_MAX_ROUNDS` → end with an explanatory reply.
- `classify_intent` failure → falls back to `question` (existing behavior).

## Testing

The `meeting/` package currently has no test suite (CLAUDE.md). This feature introduces the
first one for it:
- **Unit:** `pm_agent_client` request building + Task/artifact/DataPart parsing, against
  recorded A2A JSON-RPC fixtures (no live network).
- **Unit:** graph routing — `classify_intent` → `pm_task`; the `pm_call`/`pm_await` loop
  with a faked client returning `completed`, `need_more_info`, then `need_approval`.
- **Integration (optional, gated on env):** one live `message/send` against pm-agent's
  `redmine_query` skill behind a marker so CI can skip it.

## Open questions / risks

1. **API key provisioning** — need pm-agent's `API_SEC_KEY` value at deploy time.
2. **Identity** — static key means all Redmine actions run as one service identity (no
   `query_by` per user). Acceptable for v1; revisit if per-user attribution is required
   (would switch to the OIDC path).
3. **`message/send` blocking semantics** — confirm pm-agent returns the interrupted Task
   (INPUT_REQUIRED) as the terminal result of a non-streaming `message/send`; if it only
   surfaces interrupts over SSE, the client must use `message/stream` instead. Verify early
   in implementation.
4. **FE work** — the `need_more_info` text-reply affordance is out of scope for this spec
   (backend-only) but must be tracked for the React frontend.
