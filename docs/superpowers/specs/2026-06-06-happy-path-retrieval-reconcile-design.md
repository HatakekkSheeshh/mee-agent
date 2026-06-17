# Design: chat happy-path — title-scoped retrieval + create_task → pm-agent reconcile

**Date:** 2026-06-06
**Status:** Approved (design phase) — pending implementation
**Branch:** `feat/backend-agents`
**Builds on:** Phase 2 (pm-agent A2A loop) — `docs/superpowers/specs/2026-06-02-pm-agent-a2a-chat-design.md`

## Problem

The chat graph has three intents (`question` / `tool` / `pm_task`). Today:
- **question** answers only from the *bound* meeting's MoM, injected as a truncated blurb
  (`answer_node` in `meeting/graphs/chat_graph.py`); no title resolution, no transcript search.
- **tool / create_task** (`meeting/services/tools.py`) is a local side-effect tool; it does not
  feed pm-agent and does not read meeting content.
- **pm_task** (Phase 2) drives pm-agent over A2A with HITL — works, but is only reachable when
  the user phrases a PM request directly.

We want a coherent **happy path** across all three.

## Goals (locked decisions)

1. **question → bound-default + title-override + hybrid retrieval.**
   - Resolve the meeting: use the chat's bound `meeting_id` by default; if the user names a
     meeting by **title** in the query, resolve that meeting instead.
   - Retrieve with the existing **`memory_service`** (bge-m3 vector + tsvector keyword + RRF;
     `meeting/services/memory_service.py`) over the resolved meeting's transcript / `memory_events`
     **and** its MoM — inject the top hits instead of today's truncated MoM string.

2. **create_task → template → pm-agent `redmine_reconcile`.**
   - `create_task` pulls the meeting's MoM **action_items** and builds a task **template**:
     `{ project, items: [{subject, description, assignee, due_date}] }`.
   - It drives pm-agent's **reconcile** skill (NOT plain create): pm-agent reconciles the items
     against existing Redmine issues (create-or-update), with its own HITL pauses; we follow up
     on the same pm-agent `task_id`.

3. **pm_task** stays as built (Phase 2) and is the **execution substrate** for goal #2 — the
   `pm_call`/`pm_await` loop already handles multiple interrupts (`need_more_info`,
   `need_approval`) bounded by `PM_MAX_ROUNDS=6`.

Non-goals: per-user Redmine identity (still static `X-API-KEY`); streaming; a local Redmine
mirror table; changing pm-agent.

## Verified pm-agent reconcile contract (from `projects/pm-agent`)

- Skill id **`redmine_reconcile`** (`src/a2a_server/agent_card.py`): "Đối chiếu & tạo/cập nhật
  issues từ biên bản họp (HITL)"; **pauses twice** (reconciliation + field approval).
- Reconcile intent (`src/PM_agent/prompts.py:75`): triggered when the user wants to **đối chiếu
  một TẬP công việc** (from meeting notes/chat) against existing issues in **one** project, so the
  system decides what to CREATE vs UPDATE.
- `reconcile_check_info` node (`src/PM_agent/nodes/reconcile_check_info.py`): an **LLM extracts
  `project` + `items[]` from the natural-language message**; if `project`/`assignee` are missing
  it asks back → surfaces as **`need_more_info`** (input-required, no approval DataPart).
- Items: matched by `subject` (`reconcile_utils.prefilter_candidates` uses subject token Jaccard);
  the match step yields per-item `action ∈ {create, update}` with `issue_id` for updates
  (`normalize_reconcile_plan`). So each template item should carry at least
  `subject` (+ `description`, `assignee`, `due_date` to fill fields).
- State carries `reconcile_items` / `reconcile_plan` (`src/PM_agent/state.py`).

**Implication for us:** pm-agent parses the request itself; we don't send a raw Redmine body.
We send a **reconcile-phrased message** that clearly lists the items + the target project, and
(belt-and-suspenders) a **DataPart** mirroring `items[]` so extraction is reliable. The double
pause maps cleanly onto our existing `pm_await`.

## Architecture / changes

| Layer | File | Change |
|---|---|---|
| Repo | `meeting/db/repositories.py` | **NEW** `find_meetings_by_title(user_id, q)` (ILIKE); helper to pull MoM action_items for a meeting. |
| Graph: question | `meeting/graphs/chat_graph.py` | `classify_intent` also extracts optional `meeting_title`; a resolve step swaps `meeting_context` when a title is named; `answer_node` retrieves via `memory_service` over the resolved meeting. |
| Graph: create_task→reconcile | `meeting/graphs/chat_graph.py` | When `tool == create_task`: build the template from MoM, then enter the pm loop with a new `pm_next_payload` kind `"reconcile"` (text + items DataPart). Reuses `pm_call`/`pm_await`/`pm_reply`. |
| Tool | `meeting/services/tools.py` | `create_task` builds/returns the template (does not write locally in the happy path) — or keep its local behavior behind a flag; decide in plan. |
| Client | `meeting/services/pm_agent_client.py` | No new method needed — `send_message(text, data_part=...)` already supports the reconcile message + DataPart. |
| Memory | `meeting/services/memory_service.py` | Reused; ensure `scripts/backfill_embeddings.py` has been run so `memory_events.embedding` is populated. |

### question flow (additions in bold)
```
load_context → classify_intent ─ question →
   **resolve_meeting** (bound default; title→find_meetings_by_title) →
   **answer (memory_service hybrid retrieval over MoM + transcript)** → save_reply
```

### create_task flow
```
classify_intent ─ tool(create_task) → **build_template (from MoM action_items)** →
   pm_call(kind="reconcile": reconcile message + items DataPart) →
       (pm_await ⇄ pm_call: need_more_info → need_approval) → pm_reply → save_reply
```

## Pre-flight verification (BLOCKED on real token)

pm-agent reconcile **writes**, so this is not read-only. With a real `PM_AGENT_API_KEY`:
send one reconcile request, drive it to the final approval, and **reject** to avoid a real write —
confirming: (a) `message/send` surfaces `need_more_info` then `need_approval` in the body (Phase 2
already showed this for create), (b) the exact message/DataPart shape that makes
`reconcile_check_info` extraction reliable. Adjust the payload builder accordingly.

## Testing (extends `tests/meeting/`)

- Unit: `find_meetings_by_title` (ILIKE, user-scoped); MoM→template builder (action_items →
  `{project, items[]}`); reconcile payload builder (template → reconcile text + DataPart).
- Unit: `resolve_meeting` (title override vs bound default).
- Unit: `answer_node` with a fake `memory_service` (asserts retrieved hits are injected).
- Graph: create_task → reconcile loop with a `FakeClient` scripted
  `need_more_info → need_approval → completed` (reuses Phase 2's FakeClient harness).
- Live (gated on token): the reject-at-approval smoke above.

## Open questions / risks

1. **Token + DB at head** still required for live verification (same blocker as Phase 2).
2. **Embeddings populated?** Hybrid retrieval is only as good as `memory_events` coverage; if a
   meeting was never embedded, fall back to MoM-only retrieval gracefully.
3. **Title ambiguity** — multiple meetings match a title fragment; pick most-recent or ask the user
   (a `need_more_info`-style clarification). Decide in plan.
4. **create_task local vs pm-only** — happy path is pm-only (template producer). If a local task
   row is also wanted later, add it without changing the reconcile path.
