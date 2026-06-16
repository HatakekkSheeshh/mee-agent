# Plan: due-date picker (day/month/year) → YYYY-MM-DD for Redmine MCP

Status: PROPOSED (not started) — 2026-06-16

## Problem (also a latent bug)
The `create_task` HITL card lets the user type `due_date` as **free text** (e.g.
"06/06/2026"), and `redmine_create_args` / `redmine_update_args` (`meeting/graphs/
_chat_serde.py:240`) pass it **directly** to the MCP tool. But `create_redmine_issue`
requires **`YYYY-MM-DD`** (per `.mcp_redmine_tools_cache.json`). So a DD/MM/YYYY value —
or a MoM-derived deadline like "06/06/2026" / "Chưa xác định" — is sent raw and Redmine
**rejects or misparses** it. There's no validation either, so the user only finds out when
the issue lands with a wrong/empty due date.

## Goal
Make due-date entry unambiguous and always produce a valid `YYYY-MM-DD` for the MCP.

## Proposed change
1. **Frontend (`CreateTaskCard.tsx`):** replace the free-text due-date input (line ~194)
   with **three `<select>` pickers — Ngày / Tháng / Năm** per task item.
   - Day 1–31, Month 1–12, Year = current year .. +2 (or a small range).
   - Allow "— (trống)" so a task can have no due date.
   - Store internally as `YYYY-MM-DD` (or empty); combine on change. Pad to 2 digits
     (`day-day`, `month-month`) and 4-digit year, e.g. `2026-06-06`.
   - Pre-fill from the incoming `due_date` if it parses; else leave blank.
2. **Backend safety net (`_chat_serde.py`):** normalize `due_date` in
   `redmine_create_args` / `redmine_update_args` to `YYYY-MM-DD` regardless of source —
   parse common inputs (`DD/MM/YYYY`, `D/M/YYYY`, `YYYY-MM-DD`) and **drop** anything
   unparseable (e.g. "Chưa xác định") rather than sending garbage. This protects the
   MoM-derived path too, not just the card.
   - Add a small pure helper `to_redmine_date(s) -> str | None` (TDD: table of inputs).

## Why both layers
The picker guarantees valid input from the UI; the backend normalizer guarantees
correctness for ANY path (agent-supplied, MoM deadline, future callers) and is the
real bug fix. Pickers alone wouldn't fix MoM-derived dates.

## Tests
- `to_redmine_date`: "06/06/2026"→"2026-06-06"; "6/6/2026"→"2026-06-06";
  "2026-06-06"→"2026-06-06"; "Chưa xác định"→None; ""→None; invalid→None.
- `redmine_create_args`/`update_args`: due_date normalized; unparseable omitted.
- FE (optional): card emits `YYYY-MM-DD` from the three selects; empty when blank.

## Open questions
- Year range to show (current..+2? or free numeric year select?).
- Locale of the picker labels (VI default; EN via i18n).
- Show the assembled date back as a read-only "2026-06-06" hint next to the selects?

## Related
- `.mcp_redmine_tools_cache.json` (tool schema: `due_date` = `YYYY-MM-DD`).
- `meeting/graphs/_chat_serde.py` (redmine_create_args/redmine_update_args).
- `meeting_frontend_react/src/components/CreateTaskCard.tsx`.
