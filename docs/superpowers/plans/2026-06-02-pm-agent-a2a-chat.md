# pm-agent A2A Chat Integration — Lean Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. This is a *lean* plan: it names exact files, the contract for each unit, and what each test must prove — but does not pre-write every code block. Spec: `docs/superpowers/specs/2026-06-02-pm-agent-a2a-chat-design.md`.

**Goal:** Let the meeting chat drive pm-agent's Redmine actions over A2A, mirroring pm-agent's HITL approvals back to the chat user.

**Architecture:** New `pm_task` branch inside the existing `chat_graph.py`. A thin `httpx` A2A client calls pm-agent's `message/send`. An isolated `pm_call` node (one A2A send, no interrupt) loops with a `pm_await` node (the only `interrupt()`) until pm-agent returns `completed`/`failed`.

**Tech Stack:** Python 3.12, FastAPI, LangGraph (`interrupt()`/checkpointer), httpx, pytest (first tests for the `meeting/` package). Use `venv/bin/...`.

**Key constraint:** LangGraph replays an interrupted node from its top on resume → the non-idempotent A2A send MUST live in `pm_call`, never in the same node as `interrupt()`.

---

## Pre-flight (do first, before Task 4)

- [ ] **Confirm `message/send` blocking semantics** (spec Open Q #3). With `PM_AGENT_API_KEY` set, send one `redmine_mutate` request via `message/send` and confirm the interrupted Task comes back as `INPUT_REQUIRED` in the *response body* (not only over SSE). If interrupts only surface via SSE → switch the client to `message/stream`. Record the answer in the client module docstring.
- [ ] Confirm `httpx` imports under `venv` (transitive via `openai`); pin in `requirements.txt` only if import fails.
- [ ] Add `pytest` + `pytest-asyncio` (dev), create `tests/meeting/__init__.py`. Existing `tests/` target legacy `whisper_live` — keep `meeting/` tests under `tests/meeting/`.

---

## Task 1: A2A client — `meeting/services/pm_agent_client.py`

**Files:**
- Create: `meeting/services/pm_agent_client.py`
- Test: `tests/meeting/test_pm_agent_client.py`
- Modify: `meeting/services/__init__.py` (export `get_pm_agent_client`, `PmAgentResult`, `PmAgentError`)

**Contract:**
- `@dataclass(frozen=True) PmAgentResult(task_id, state, text, need_approval, issues)`, `state ∈ {"completed","failed","input_required","working"}`.
- `class PmAgentError(Exception)`.
- `async def send_message(text, *, task_id=None, data_part=None) -> PmAgentResult`
- `async def cancel(task_id) -> None`
- Reads `PM_AGENT_A2A_URL`, `PM_AGENT_API_KEY` from env (per-service, no shared client). `X-API-KEY` header. JSON-RPC method `message/send`; TextPart for `text`, append DataPart when `data_part` given. Parse Task → map `status.state`, join text artifacts/status message into `text`, detect `approval_request` DataPart → `need_approval` + `issues`, read `task.id` → `task_id`. Non-2xx / transport / timeout → `PmAgentError`.

**Tests (no live network — stub httpx transport / monkeypatch the POST with recorded JSON-RPC dicts):**
- [ ] `test_send_message_builds_jsonrpc_with_api_key` — method `message/send`, text in TextPart, `X-API-KEY` header present.
- [ ] `test_resume_includes_task_id_and_datapart` — `task_id` + `data_part={"approval_action":"approve"}` → request reuses task id, includes DataPart.
- [ ] `test_parse_completed` — completed Task + text artifact → `state="completed"`, `text` set, `need_approval=False`.
- [ ] `test_parse_need_approval` — INPUT_REQUIRED + `approval_request` DataPart → `state="input_required"`, `need_approval=True`, `issues` parsed.
- [ ] `test_parse_need_more_info` — INPUT_REQUIRED text-only → `state="input_required"`, `need_approval=False`.
- [ ] `test_http_error_raises_pmagenterror` — non-200 / timeout → `PmAgentError`.
- [ ] Loop each: write test → `venv/bin/python -m pytest tests/meeting/test_pm_agent_client.py -v` (fail) → implement → pass.

**Commit:** `feat(pm-agent): add A2A JSON-RPC client`

---

## Task 2: `.env.example` + config

**Files:** Modify `.env.example`

- [ ] Add `pm-agent (A2A)` block: `PM_AGENT_A2A_URL=https://endpoint-e05e38a1-6070-4f9d-8ab2-80ea965ec2f6.agentbase-runtime.aiplatform.vngcloud.vn/a2a/` and `PM_AGENT_API_KEY=your-pm-agent-api-key-here`. No real secret.
- [ ] Commit: `chore(env): document PM_AGENT_* vars`

---

## Task 3: Classification — extend `classify_intent`

**Files:** Modify `meeting/graphs/chat_graph.py` (`classify_intent` prompt + `route_after_classify`); Test: `tests/meeting/test_chat_routing.py`

**Contract:** `intent ∈ {"question","tool","pm_task"}`. Prompt gains one paragraph on pm-agent skills (query/report issues; create/update with approval). `route_after_classify` returns `"pm_call"` when `intent=="pm_task"`.

**Tests (monkeypatch LLM client → canned JSON):**
- [ ] `test_route_pm_task_goes_to_pm_call`.
- [ ] `test_route_question_unchanged`, `test_route_tool_unchanged` (regression).
- [ ] Write → fail → implement → pass → commit: `feat(chat): classify pm_task intent`

---

## Task 4: pm_call / pm_await / pm_reply nodes + looped edges

**Files:** Modify `meeting/graphs/chat_graph.py` (ChatState fields, 3 nodes, edges, builder); Test: `tests/meeting/test_pm_graph_loop.py`

**ChatState additions:** `pm_task_id`, `pm_next_payload`, `pm_last`, `pm_pending`, `pm_rounds` (spec §2 table). Const `PM_MAX_ROUNDS = 6`.

**Nodes:**
- `pm_call`: build args from `pm_next_payload` (`start`→text only; `text`→text+task_id; `approval`→task_id+DataPart), call `get_pm_agent_client().send_message(...)`, increment `pm_rounds`, write `pm_task_id`/`pm_last`. `PmAgentError` → VI error `final_reply` + `tool_result.error`, route to `save_reply`. `pm_rounds > PM_MAX_ROUNDS` → explanatory reply, end.
- conditional edge after `pm_call`: `completed|failed` → `pm_reply`; `input_required` → `pm_await`.
- `pm_await`: build `pm_pending` (need_approval → `{kind:"need_approval", issues}`; else `{kind:"need_more_info", prompt}`), `decision = interrupt(pm_pending)`, map `decision` → `pm_next_payload` (`approve/edit/reject` → approval payload; free text → `{kind:"text"}`), edge → `pm_call`.
- `pm_reply`: `final_reply = pm_last.text`, edge → `save_reply`.
- Builder: add nodes; `route_after_classify` maps `"pm_call"`; add conditional edge + `pm_await→pm_call` + `pm_reply→save_reply`. Provide a seam to inject a fake client (`build_chat_graph(..., pm_client=None)` defaulting to `get_pm_agent_client()`).

**Tests (inject FakeClient):**
- [ ] `test_pm_call_completed_reply` — `completed` → ends, `final_reply==text`, exactly **one** send.
- [ ] `test_pm_call_need_approval_interrupts` — `need_approval` → `__interrupt__` carries `issues`; no 2nd send pre-resume.
- [ ] `test_resume_approve_sends_datapart` — resume `{action:"approved", approval_action:"approve"}` → 2nd call has DataPart + same `task_id`; then `completed` → reply.
- [ ] `test_need_more_info_then_approval_then_done` — need_more_info → (resume text) → need_approval → (resume approve) → completed; assert no double-send across both interrupts (replay-safety proof).
- [ ] `test_pm_error_graceful_reply` — `PmAgentError` → VI error reply, ends.
- [ ] `test_max_rounds_cap` — always input_required → stops at `PM_MAX_ROUNDS`.
- [ ] Write → fail → implement → pass → commit: `feat(chat): pm-agent A2A loop with HITL`

---

## Task 5: HTTP layer — widen resume payload (`meeting/api/chat.py`)

**Files:** Modify `meeting/api/chat.py`; Test: `tests/meeting/test_chat_api_pm.py`

**Contract:**
- `ApprovalRequest` gains optional `approval_action: str | None`, `text: str | None`.
- `send_message`: when graph interrupts on a pm step, persist `PendingAction` with `tool_name="pm_agent"`, `tool_args=pm_pending`. Response shape unchanged.
- `approve_action`/`reject_action`: when `action.tool_name=="pm_agent"`, build decision carrying `approval_action`/`text`, then `resume_chat_turn` as today. Local-tool path unchanged.

**Tests (FastAPI TestClient / httpx ASGI; FakeClient + test DB session):**
- [ ] `test_pm_message_returns_pending_action` — pm_task message → `status:"interrupted"`, pending_action `tool="pm_agent"` with issues.
- [ ] `test_approve_pm_resumes_with_action` — approve with `approval_action:"approve"` → executed reply.
- [ ] `test_provide_more_info_text` — need_more_info pending + approve with `text` → resumes with free text.
- [ ] `test_local_tool_approval_still_works` — regression on existing send_email/create_task path.
- [ ] Write → fail → implement → pass → commit: `feat(api): pm-agent approval/resume payload`

---

## Task 6: Wire-up + manual smoke

**Files:** Modify `meeting/graphs/__init__.py` only if a new exported helper was added (keep `run_chat_turn`/`resume_chat_turn` signatures stable).

- [ ] `venv/bin/python -m pytest tests/meeting -v` → all pass.
- [ ] With real key in `.env`: `venv/bin/python run_meeting.py`, create chat session, send "liệt kê issue overdue" (read-only) → real pm-agent reply. Then a create request → approval card → approve → Redmine write. (Manual; document in PR.)
- [ ] Commit wire-up if any: `chore(chat): wire pm-agent client into graph`

---

## Deferred (NOT in this plan)

- **transcript_segments injection** — owner specifies trigger/shape later (spec §5). Leave a clear comment in `pm_call` marking where transcript context folds into the request text. No repo/prompt/schema change now (YAGNI).
- **React FE** `need_more_info` text-reply affordance (spec Open Q #4) — backend additive; FE tracked separately.
- **Per-user identity** via Microsoft OIDC (spec risk #2) — static key is v1.

## Self-review

- Coverage: spec §1→T1, §2→T2, §3→T3, graph §2→T4, HTTP §4→T5, errors §7→T4/T5 tests, testing §→all tasks, §5 deferred→Deferred. No gaps.
- Replay-safety exercised by `test_need_more_info_then_approval_then_done`.
- Names consistent: `send_message`, `PmAgentResult`, `PmAgentError`, `pm_call`, `pm_await`, `pm_reply`, `pm_next_payload`, `PM_MAX_ROUNDS`.
