# Design: create_task → pm-agent reconcile bridge

**Date:** 2026-06-08
**Status:** Approved (design phase) — pending implementation
**Branch:** `feat/backend-agents`
**Builds on:**
- `docs/superpowers/specs/2026-06-06-happy-path-retrieval-reconcile-design.md` (goal #2)
- Task #8 unified tool-calling agent (`plans/2026-06-08-unified-qa-tool-agent.md`)
- Phase 2 pm-agent A2A loop (`specs/2026-06-02-pm-agent-a2a-chat-design.md`)

## Problem

`create_task` (a side-effect tool in the unified `agent` branch) builds a structured
task list from the meeting's MoM `action_items` but only marks it `"prepared"` — it
never reaches Redmine. The separate `pm_task` branch already drives pm-agent's
`redmine_reconcile` skill over A2A with HITL, but it's only reachable when the user
phrases a direct Redmine request, and pm-agent then has to *ask* what the items are
(`need_more_info`) because the chat message alone doesn't carry them.

We want tasks derived from a meeting's minutes to flow into Redmine **automatically**:
the agent builds the item list from the MoM, the user reviews it once, and pm-agent
reconciles it (create-or-update) against existing issues with its own write approval.

## Goals (locked decisions)

1. **`create_task` bridges into the pm reconcile loop.** On approval, the agent branch
   builds the MoM template and transitions into the existing `pm_call`/`pm_await`
   reconcile loop (new `pm_next_payload` kind `"reconcile"`). One tool, automatic handoff.
2. **Two HITL gates.**
   - **GATE 1 (local):** before anything leaves Mee, the user reviews the built template
     on the local `create_task` approval card.
   - **GATE 2 (pm-agent):** pm-agent's own `need_approval` step before the real Redmine
     write (create/update issues).
3. **Project pre-filled, editable.** The target Redmine project defaults to the bound
   meeting's **title** and is shown on GATE 1's card as an **editable** field; the user
   can adjust it before the reconcile request is sent.

Non-goals: per-user Redmine identity; streaming; a local Redmine mirror; changing
pm-agent; FE rework beyond accepting an edited `project` (see Risks).

## Verified pm-agent reconcile contract (from 2026-06-06 spec)

- Skill `redmine_reconcile`: "Đối chiếu & tạo/cập nhật issues từ biên bản họp (HITL)";
  **pauses twice** (reconciliation preview + field approval).
- `reconcile_check_info` LLM extracts `project` + `items[]` from the message; missing
  `project`/`assignee` → `need_more_info` (input-required, no approval DataPart).
- Items matched by `subject`; each yields `action ∈ {create, update}`. So each template
  item carries `subject` (+ `description`, `assignee`, `due_date`).
- **Implication:** we send a reconcile-phrased message listing items + project AND a
  DataPart mirroring `items[]` so extraction is reliable. The double pause maps onto our
  existing `pm_await`.

## Flow

```
agent → create_task (side-effect)
   → agent_tools: build template (read MoM action_items; project = meeting title)
   → agent_approve            [GATE 1: card shows {project (editable), items[]}]
   → agent_execute (approved) → build reconcile payload → route to pm_call
   → pm_call(kind="reconcile": reconcile text + items DataPart)
   → pm_await                 [GATE 2: pm-agent need_approval — Redmine create/update preview]
   → pm_call(approval) → pm_reply → save_reply
```

Reject at GATE 1 → `agent_execute` records the rejection, routes back to `agent`, which
replies (no handoff). The two interrupts chain across two `approve` API calls;
`api/chat.py` already re-persists a fresh pending action when a resume interrupts again
(`_persist_interrupt` in `approve_action`), so **no API change**.

## Architecture / changes

| Layer | File | Change |
|---|---|---|
| Tool | `meeting/services/tools.py` | Extract template builder from `_exec_create_task` → returns `{project, items:[{subject, description, assignee, due_date}]}`; `project` default = meeting title. Reused by `agent_tools`. |
| Graph: build | `meeting/graphs/chat_graph.py` | In `agent_tools`, when the deferred side-effect tool is `create_task`, build the template (read-only) so `pending_tool.args = {project, items}`. Mirrors the `switch_meeting` special-case. |
| Graph: bridge | `meeting/graphs/chat_graph.py` | `agent_execute`: on approved `create_task`, merge `edited_args` (e.g. edited `project`) into the template, set `pm_next_payload={kind:"reconcile", project, items, text}`, route to `pm_call`. On reject: route to `agent`. |
| Graph: edge | `meeting/graphs/chat_graph.py` | Replace plain edge `agent_execute → agent` with conditional `route_after_agent_execute`: `"reconcile" → pm_call`, else `→ agent`. |
| Graph: pm | `meeting/graphs/chat_graph.py` | `pm_call` handles `kind="reconcile"`: send reconcile text + `data_part` mirroring `items[]`. |
| Graph: routing | `meeting/graphs/chat_graph.py` | `classify_intent` example: "create tasks **from the meeting** (onto Redmine)" → `agent`; pure Redmine ops stay `pm_task`. |
| Client | `meeting/services/pm_agent_client.py` | No change — `send_message(text, data_part=...)` already supports it. |

### Reconcile payload shape

```python
pm_next_payload = {
    "kind": "reconcile",
    "project": "<editable, default = meeting title>",
    "items": [{"subject", "description", "assignee", "due_date"}, ...],
    "text": "Đối chiếu và tạo/cập nhật các công việc sau trên dự án {project}:\n"
            "1. {subject} — phụ trách {assignee}, hạn {due_date}\n...",
}
# pm_call sends: send_message(text, data_part={"kind":"reconcile_items","project","items"})
```

## Testing (TDD, extends `tests/meeting/`)

- Unit: template builder — MoM `action_items` → `{project, items}`; project default = title;
  explicit single-task path; empty MoM → error.
- Unit: reconcile payload builder — template → reconcile `text` (lists items + project) +
  DataPart `{project, items}`.
- Unit: `pm_call` with `kind="reconcile"` — FakeClient asserts `text` + `data_part` sent.
- Unit: `route_after_agent_execute` — reconcile → `pm_call`; normal → `agent`.
- Graph: full bridge — `create_task` → GATE 1 approve (with edited `project`) →
  `pm_call(reconcile)` → `pm_await(need_approval)` → approve → `pm_reply`. Reuses Phase 2's
  FakeClient. Plus a reject-at-GATE-1 variant (no handoff, agent replies).

## Open questions / risks

1. **FE editing of `project`.** GATE 1's card must let the user edit `project`. Backend
   accepts `edited_args.project` regardless; a small FE input may be a follow-up. This PR
   is backend-only unless the FE is requested.
2. **PM_AGENT config required.** This is a Redmine-write feature; without `PM_AGENT_*`,
   `pm_call` returns a connection-error reply (graceful).
3. **Live verification still gated on a real token + DB at head** (same blocker as Phase 2 /
   the 2026-06-06 spec). Unit/graph coverage lands now; the reject-at-approval live smoke
   follows when the token is available.
4. **classify ambiguity.** "tạo task ... Redmine" could route either way; the added example
   biases meeting-derived tasks to `agent`. Tune in implementation if needed.
