# Deterministic formatter for Redmine issue-list reads

**Date:** 2026-06-12
**Branch:** `feat/personalized-user-prompt`
**Status:** Design approved — ready for implementation plan

## Problem

When the chat agent runs a Redmine read tool that returns a list of issues, the
raw MCP JSON is dumped into a `{"role":"tool"}` message (`agent.py:286`) and the
LLM hand-renders the markdown table on its next round. On a 31-row
`get_stale_issues` result for "AI Innovation Projects", the rendered table showed
issue **#28815** with blank subject/assignee/last-updated.

### Diagnosis (confirmed via live MCP probe, 2026-06-12)

Hypothesis 1 (null source data) is **refuted**; Hypothesis 2 (LLM render error)
is **confirmed**:

- `get_stale_issues(project="AI Innovation Projects", days=3)` and `days=7` both
  return **exactly 31 rows** — the reported table.
- In that result #28815 comes back **fully populated**: `subject="[RnD] Tìm hiểu
  về AI agent"`, `assigned_to="hieunq3@vng.com.vn"`, `last_updated="2026-06-04"`.
  `get_redmine_issue_by_id(28815)` independently confirms the same.
- #28815 is actually **row 22 of 31**, not the last row as reported — the
  misplacement is itself a fingerprint of the model garbling the table.
- The only genuinely-blank cells in the whole result are two truly-unassigned
  rows (`#29340`, `#29243`, `assigned_to=None`).

**Root cause:** the model (minimax-m2.5) mangles rows when hand-rendering a large
markdown table from raw JSON. The data layer is correct; rendering is the
failure. Fix = build the table in code, skip the LLM render round.

## Goal

Render issue-list read results deterministically in code so the table is exact,
never truncated, with explicit `—` for null fields — and never re-rendered by the
LLM.

## Scope (v1)

Five tools share one row schema (`issue_id, subject, status, tracker,
assigned_to, last_updated, url`), so **one renderer** covers all of them:

- `get_stale_issues` (the reported bug)
- `get_overdue_issues`
- `get_issues_due_soon`
- `get_unassigned_issues`
- `list_redmine_issue`

Out of scope for v1 (keep current LLM rendering): `get_workload_by_assignee`,
`get_version_progress`, `get_project_updates`, `get_issue_children_recursive`,
`get_redmine_issue_by_id`, `get_redmine_projects`, `get_field_metadata`. These
have different shapes and would need bespoke renderers — deferred (YAGNI).

## Approach

**Short-circuit** the agent loop for pure read-display turns: when the round
consists *only* of formatter-registered read tools, render the table in code and
return it as `final_reply`, skipping the second LLM round entirely. Chosen over
"LLM narrates + table appended" (more moving parts) and "pass-through hint"
(relies on the same model that mangled it). Falls back to current behavior for
any tool without a formatter, mixed rounds, or side-effect tools.

## Design

### 1. New pure module — `meeting/graphs/chat_graph/redmine_format.py`

No I/O; fully unit-testable. Public surface:

- `FORMATTABLE_READ_TOOLS: frozenset[str]` — the five tool names above.
- `is_formattable(name: str) -> bool`
- `format_issue_list(tool_name: str, args: dict, result: dict) -> str | None`
  - Returns **`None`** on an `{"error": ...}` result or any unrecognized shape →
    the caller falls back to the current LLM flow (never worse than today).
  - Returns a **friendly string** (`"Không có issue nào …"`) for 0 rows — still
    short-circuits, since the data is fine, just empty.
  - Otherwise returns the grouped markdown.

Internal helpers:

- `_extract_rows(result) -> list[dict] | None` — locate the issues list under
  `issues` / `result` / `data` / `items`; bare list accepted; else `None`.
- `_title(tool_name, args, count) -> str` — per-tool Vietnamese header line
  including `project_name` (omitted when absent, e.g. `list_redmine_issue` scoped
  to the auth user) and the `days` threshold where the tool takes one:
  - stale → `"{n} issue chưa cập nhật (≥{days} ngày) — {project}"`
  - overdue → `"{n} issue quá hạn — {project}"`
  - due_soon → `"{n} issue sắp đến hạn (trong {days} ngày) — {project}"`
  - unassigned → `"{n} issue chưa có người phụ trách — {project}"`
  - list → `"{n} issue — {project}"`
- `_clean(s) -> str` — escape `|`, strip newlines, collapse whitespace; `None`/
  empty → `—`.
- `_assignee(s) -> str` — strip `@vng.com.vn` / `@vng.vn` domain for compactness;
  `None`/empty → `—`.
- `_group_and_sort(rows)` — group by `status` in canonical order
  `["New", "In Progress", "Resolved", "Feedback", "Closed", "Rejected"]`, unknown
  statuses appended alphabetically; within each group sort by `last_updated`
  ascending (oldest / stalest first), null dates last. ISO date strings sort
  lexicographically.
- `_render_table(rows) -> str` — one markdown table per status group with a
  `### {status} ({count})` heading. Columns: `#` (rendered as `[issue_id](url)`
  when `url` present, else plain `issue_id`) · `Subject` · `Assignee` · `Updated`.

### 2. Short-circuit in `agent_tools` (`agent.py`)

After the existing tool-call loop, when `pending is None` and there was ≥1 tool
call and **every** call name `is_formattable`:

- Render each call via `format_issue_list(name, args, result)` (collect
  `(name, args, result)` during the loop).
- If all renders succeed (none `None`): set `final_reply` = renders joined with
  `"\n\n"`, `tool_result = {"status":"ok","via":"redmine_read","tools":[...],
  "count":N}`, and `agent_route = "done"`.
- If any render returns `None`, or the round is mixed / has a side-effect tool:
  do **not** short-circuit — fall through with `agent_route="agent"` exactly as
  today.

The raw `{"role":"tool"}` messages are still appended to `agent_messages` (history
stays valid if the conversation continues).

### 3. Routing (one new route value)

- `route_after_agent_tools` (`agent.py`): return `"save_reply"` when
  `agent_route == "done"` (existing returns: `agent`, `agent_approve`).
- `builder.py` agent_tools conditional-edge map: add
  `"save_reply": "save_reply"` alongside the existing two targets.

### Data flow

```
agent (LLM emits get_stale_issues call)
  → agent_tools: execute read tool → append raw tool msg
                 all calls formattable? → format_issue_list() in code
                   → final_reply set, agent_route="done"
  → route_after_agent_tools == "save_reply"
  → save_reply → END        (NO second LLM round; rows never re-rendered)
```

### Error handling

- Tool returned `{"error": ...}` → `format_issue_list` returns `None` → fall back
  to LLM so the model can explain the error to the user.
- Unrecognized result shape → `None` → fall back. The formatter is best-effort;
  it never degrades the existing path.

## Testing — `tests/meeting/test_redmine_format.py` (no network)

Pure formatter:
- null assignee → `—`; pipe (`|`) escaping; newline stripping in subject.
- status grouping + canonical order; oldest-first sort within group; null
  `last_updated` sorts last.
- 0 rows → friendly empty message (not an empty table).
- `{"error": ...}` result → `None`; unrecognized shape → `None`.
- per-tool header text (stale shows `days`; overdue/due_soon/unassigned/list
  variants; project omitted when absent).
- `#` cell renders `[id](url)` when `url` present.
- **#28815 regression:** a 31-row fixture renders all 31 rows with #28815 fully
  populated (subject/assignee/updated present).

`agent_tools` integration (fake tool results, no network):
- pure-formattable round → `final_reply` set, `agent_route="done"`.
- mixed round (formattable read + non-formattable read) → no short-circuit.
- formattable read returning `{"error":...}` → no short-circuit.

## Known tradeoff (v1, explicit)

A "find-then-act" turn where the model calls `list_redmine_issue` **alone** first
will short-circuit (show the list) and end the turn; the user then follows up to
act. The other four tools are inherently terminal reports, so only
`list_redmine_issue` is affected. Acceptable for v1 — the user sees the full list
and re-asks.

## Files touched

- **new** `meeting/graphs/chat_graph/redmine_format.py`
- **edit** `meeting/graphs/chat_graph/agent.py` (`agent_tools` short-circuit;
  `route_after_agent_tools` new return)
- **edit** `meeting/graphs/chat_graph/builder.py` (agent_tools edge map)
- **new** `tests/meeting/test_redmine_format.py`

## Out of scope / orthogonal

- Bespoke renderers for the other 7 read tools (deferred).
- The uncommitted `docs/diagrams/chat_graph.mmd` (P2 diagram regen) — separate
  housekeeping commit.
- `scripts/diag_28815.py` — throwaway diagnostic used for this investigation;
  keep as a sibling of `probe_redmine_mcp.py` or discard.
