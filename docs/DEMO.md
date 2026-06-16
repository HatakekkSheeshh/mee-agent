# Mee — Demo Script (5 reliable use cases)

These 5 cover the **chat-captured agent memory** feature (remember / forget / recall /
inspect). They're the ones to demo with confidence: built + unit-tested this session
(457 tests green) and confirmed live (agent recalled the nickname; `dump_agent_memory.py`
verified). Each is **Say / Expect / Behind the scenes / Reset**.

### Prerequisites (one-time)
- Backend running the current code: `venv/bin/python run_meeting.py` (restart if it predates this work).
- `.env` has `MEMORY_ID` set (AgentBase configured) — else memory no-ops by design.
- Signed in with Microsoft (so the user has an `ms_oid` for user-scoped facts).
- Have your `ms_oid` handy for the inspect step:
  `SELECT ms_oid FROM users WHERE email='<you>@vng.com.vn';`

---

## 1. Remember a personal nickname (user scope)
- **Say:** `Gọi tôi là Ronaldo nhé.`
- **Expect:** the agent acknowledges and stores it (no approval popup — it runs silently).
  The "tools used" chip shows **"Ghi nhớ giúp bạn"**.
- **Behind the scenes:** `remember_fact(text="…Ronaldo…", scope="user")` →
  `user_prefs/<ms_oid>`, written in the background.
- **Reset:** use case 3 (forget), or it simply persists for the next demo step.

## 2. Recall it in a brand-new session
- **Do:** open a **new chat session** (sidebar → new), then **Say:** `Chào bạn` or `Tôi là ai?`
- **Expect:** the agent addresses you as **Ronaldo** — even though this session has no
  prior messages. This is the headline: memory survives "Xóa hội thoại" and new sessions.
- **Behind the scenes:** `load_context` recalls the fact from `user_prefs/<ms_oid>` into
  the prompt's **"Ghi nhớ"** block (user facts surface even with no meeting selected).

## 3. Ask about a specific session's minutes (MoM Q&A)
- **Say (with a project selected):** `Tóm tắt nhanh Meeting 1 giúp tôi.`
  or `Các task tôi cần làm ở Meeting 4?`
- **Expect:** the agent resolves the named session, then answers from **that session's
  MoM** — agenda / decisions / action items. For "task tôi cần làm" it filters action
  items to **you** (assignee match, e.g. "Hiếu" ↔ `hieunq3`).
- **Behind the scenes:** `list_recordings` (resolve "Meeting N" → recording_id) →
  `recording_mom` (read that session's exact items). "Meeting N / Phiên N" is treated as a
  **session in the current project**, not a different project.
- **⚠ Verify before demo:** this is the flow that previously thrashed (the GOAT bug); the
  fix shipped in `f5e0b82` (no more `switch_meeting` on "Meeting N", graceful answer on the
  round cap) — do one dry run after restarting the backend so you're confident live.

## 4. Query Redmine via MCP (read)
- **Say:** `Các dự án Redmine của tôi?` → `get_redmine_projects`
  · `Issue #29551 có title là gì?` → read by id
  · `Việc nào của tôi sắp tới hạn?` → `get_issues_due_soon`
- **Expect:** a clean, **deterministically rendered** table/answer (the agent never
  re-renders the rows — that's the #28815 fix), scoped to **your** Redmine identity.
- **Behind the scenes:** Redmine tools are discovered at runtime over MCP
  (`.mcp_redmine_tools_cache.json`) and called with **your per-user Redmine key**.
- **Prereq:** your Redmine identity must be linked — check `GET /api/redmine/status`; if
  not, the agent surfaces a "link Redmine" prompt instead of data.

## 5. Turn meeting action items into Redmine tasks (HITL + MCP write)
- **Say (with a project selected):** `Tạo task trên Redmine cho các việc trong cuộc họp này.`
  (or scope it: `Tạo task cho các việc của Hiếu trong Meeting 4.`)
- **Expect:** the agent builds an **editable task list** from the MoM action items and shows
  an **approval card**. You review/edit → **Approve** → it creates the issues and replies
  "đồng bộ N/M việc". Nothing is written until you approve.
- **Behind the scenes:** `create_task` (HITL-gated) builds the batch from MoM; on approve,
  `agent_execute` loops `create_redmine_issue` / `update_redmine_issue` over each item via
  MCP. This is the flagship **HITL + MCP write** demo.
- **⚠ Verify before demo:** writes to real Redmine under your key — do a dry run on a test
  project/issue first.

---

### Confidence note
Use cases **1–2** are the safest (built + unit-tested + you confirmed recall live).
**3–5** are real, well-understood code paths but depend on the **live model behaving** and
**Redmine being reachable with your key** — they were **not** live-verified this session
(and #3 had a bug that was just fixed). Do one rehearsal of 3–5 before presenting.

---

### Demo tips
- Memory writes run **in the background** — the reply comes back instantly; the AgentBase
  record lands a moment later. If demoing step 5 right after step 1, wait ~1–2s.
- If `MEMORY_ID` is unset, the agent stays friendly but nothing persists (returns
  "disabled" internally) — verify `.env` before the demo.
- Keep nicknames simple/unique (e.g. "Ronaldo") so recall is easy to point at on screen.
