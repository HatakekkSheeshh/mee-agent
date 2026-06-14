# Role-Persona Proactive Kickoff — Design Spec

**Branch:** `feat/personalized-user-prompt` (the branch's headline feature)
**Status:** Design approved 2026-06-13. Spec for a fresh-session TDD build.
**Memory:** `role-persona-kickoff-feature`, `agentbase-memory-api-setup`, `redmine-mcp-migration-plan`, `db-alembic-drift-remote-ahead`.

## Goal

When a user opens a chat, **Mee speaks first** with a greeting tailored to the
user's **role**, grounded in that user's live data. Two motivating examples:

- **Applied AI Intern** → "Hi, I'm Mee — today your tasks are… As an Applied AI
  Intern I'd prioritize…" (own-task focus).
- **BA** → "Hi… there are X new tasks across Y projects you're on — want to
  review?" (cross-project overview).

## Decisions (locked in brainstorm 2026-06-13)

1. **Role pool storage** = **Postgres `roles` table** (authoritative, enumerable,
   editable). NOT AgentBase — AgentBase is insert-only / no-delete /
   similarity-recall (per `agentbase-memory-api-setup`), wrong shape for a catalog.
2. **User identity (v1)** = single `get_or_create_dev_user` with a **settable
   role**; real multi-user auth (Email/UID from Microsoft) is a deferred separate
   project.
3. **Persona storage** = AgentBase **`user_prefs/{actorId}`** (USER_PREFERENCES
   strategy) — holds the user's role. (This is the `mee-user-persona` store seen
   in traces.)
4. **Kickoff** = **LLM-generated, data-grounded** — one LLM call over
   `{role.description + role.kickoff_prompt + the user's live tasks/projects}`.
5. **UX** = **auto first agent message on chat-open** when the thread is empty.

## Architecture / components (each small + testable)

1. **`roles` table + repo** — schema `{id, name UNIQUE, description, data_plan,
   kickoff_prompt, created_at}`; `repo.get_role(name)`, `repo.list_roles()`.
   `data_plan` ∈ {`own_tasks`, `cross_project`, `minimal`} makes the pool fully
   data-driven (add a role = insert a row, no code change). Alembic migration +
   a seed of the 10 company roles — see Seed + Migration notes.
2. **Persona read** — extend `meeting/memory_client.py`:
   `get_user_role(actor_id) -> str | None` reading AgentBase
   `user_prefs/{actorId}` (mirror the existing `search_project_record`
   pattern: sync urllib in a thread, best-effort, returns None on miss/error).
3. **Role→data mapping** — pure function `role_data_plan(role) -> spec` that
   reads `role.data_plan` (a column, not hardcoded names) and chooses the Redmine
   MCP reads to run:
   - `own_tasks` → own assigned tasks (`get_workload_by_assignee` / `list_redmine_issue`)
   - `cross_project` → cross-project new/unassigned (`list_redmine_issue` across
     projects / `get_unassigned_issues`)
   - `minimal` (also default/unknown role) → no data; generic greeting.
   Reuses existing Redmine MCP read tools (see `redmine-mcp-migration-plan`).
4. **Kickoff builder** — `build_kickoff_messages(role, data) -> messages` (pure
   prompt assembly) + a single LLM call (reuse `_llm_client`/`_llm_model`, strip
   `<think>`). Returns greeting text. The LLM call is the only side-effect.
5. **Backend entry** — `POST /api/chat/sessions/{id}/kickoff` → resolves the
   session's user → `get_user_role` → `get_role` (pool) → fetch role data →
   `build_kickoff_messages` → LLM → greeting. **Persist** the greeting as an
   `agent` message in `chat_messages` so it survives refresh and lands in history.
   Returns `{reply}`.
6. **FE (`ChatPane`)** — on mount/session-open, if the thread is **empty** (no
   messages, no pending), call the kickoff endpoint once and render the returned
   greeting as the first agent bubble. Guard against double-fire (a ref/flag).
   Keep the WelcomeBanner only as the no-role / failure fallback.

## Data flow

```
open chat → ensureSession → thread empty?
  └─ yes → POST /sessions/{id}/kickoff
            → resolve user (dev user) → get_user_role(user_prefs/{actorId})
            → get_role(name) from roles table
            → role_data_plan → Redmine MCP reads (own vs cross-project)
            → build_kickoff_messages → LLM → greeting
            → persist as agent message → return {reply}
       → FE renders greeting as first agent message
```

## Error handling (never block chat)

- No persona / no role → skip kickoff, show today's generic WelcomeBanner.
- Role not in pool → default greeting (no data fetch).
- Redmine MCP unreachable → greeting from role text only, omit the data line.
- LLM failure → static per-role fallback string; never 500 the chat open.

## Testing (TDD; LLM + AgentBase + MCP mocked)

- `repo.get_role` / `list_roles` against a seeded test row.
- `get_user_role` — parses role from a fake `user_prefs` record; None on miss/error.
- `role_data_plan` — pure: correct read-set per `data_plan` value + minimal default.
- `build_kickoff_messages` — pure: includes description + kickoff_prompt + data;
  shape stable.
- kickoff endpoint — happy path (greeting persisted + returned) and each
  fallback (no role, MCP down, LLM error) returns gracefully.

## Migration note (IMPORTANT — `db-alembic-drift-remote-ahead`)

The shared prod DB is stamped **past** the repo's Alembic head, and the backend
is run **without** `alembic upgrade head`. So the new `roles` migration must be
authored against the repo head, but **applying it to the shared DB needs an
explicit, careful step** (the table won't exist just by booting). Build task:
generate the migration, confirm the repo head lineage, and document/apply the
`roles` table creation to the shared DB out-of-band. Consider an idempotent
`CREATE TABLE IF NOT EXISTS` safety or a one-off apply script if the drift makes
`alembic upgrade` unsafe.

## v1 scope / YAGNI

- Single dev user; role read from `user_prefs/{actorId}` (settable — seed it via
  a small write or the existing memory write path).
- **10 company roles seeded** across 3 depts (see Seed section) + a default fallback.
- Auto-kickoff on empty thread only (not on every message).
- **Deferred:** real multi-user identity/auth; a pool-admin UI; per-org role
  customization; richer per-role data templates.

## Open verification for the build (confirm in code, fresh session)

- Exact `memory_client` read shape for `user_prefs/{actorId}` (does a persona
  record exist / how is role encoded — a line in the record text? a key?).
- How `actorId` is derived for the dev user (the AgentBase actor used today).
- `ChatPane` mount/empty-thread hook point + the `api.chat` client method to add.
- Whether to fold kickoff into `create_session` vs a separate endpoint (spec
  assumes separate; revisit if it simplifies the FE).

## Kickoff prompts (v1 draft)

### Builder meta-prompt (system) — assembled by `build_kickoff_messages`
Slots: `{user_name}`, `{role_name}`, `{role_description}`, `{role_kickoff_prompt}`,
`{role_data}` (the fetched Redmine summary; empty string if none).

```
Bạn là Mee — trợ lý cuộc họp. Bạn đang CHỦ ĐỘNG mở đầu cuộc trò chuyện
(người dùng chưa nhắn gì). Người dùng: {user_name} — vai trò: {role_name}.
Mô tả vai trò: {role_description}
Định hướng mở đầu cho vai trò này: {role_kickoff_prompt}

Dữ liệu thực tế của người dùng hôm nay (nguồn DUY NHẤT, không bịa thêm):
{role_data}

Viết MỘT lời chào mở đầu bằng tiếng Việt:
- Xưng "Mee", chào hợp với vai trò.
- Bám SÁT dữ liệu trên (đúng số task, đúng tên project). Nếu không có dữ liệu,
  chào ngắn và mời người dùng bắt đầu — TUYỆT ĐỐI không bịa số liệu.
- Kết bằng một đề xuất/câu hỏi mời hành động (vd "bạn muốn xem/tạo task không?").
- 2–4 câu, tự nhiên, không markdown nặng, không liệt kê dài.
```

### Seed `roles` rows (10 company roles, 3 depts)

`name` is the seed key (UNIQUE). `description` is the company's VI description.
`data_plan` drives `role_data_plan`. ("Intern"/"Associate"/"Lead" etc. carry
seniority only — the **role name is the unit**, not the person.)

**User→role is decoupled from the seed** — it's settable persona data
(`user_prefs/{actorId}`), not baked into the role pool. The build focuses on the
role + kickoff behavior, NOT on hardcoding which user has which role. Illustrative
only (not authoritative): annd2 → Software Engineer, hieunq3/nhihb → AI Applied,
locdt4 → AI Engineer.

#### Dept: Engineer

- **AI Applied** — `data_plan`: `own_tasks`
  `description`: "Nghiên cứu và ứng dụng các mô hình trí tuệ nhân tạo vào sản
  phẩm thực tế, tối ưu hóa thuật toán để giải quyết các bài toán cụ thể của
  doanh nghiệp."
  `kickoff_prompt`: "Tập trung vào CÔNG VIỆC CỦA RIÊNG người dùng: điểm qua các
  task nghiên cứu/ứng dụng mô hình đang được giao, gợi ý ưu tiên theo hạn và mức
  độ quan trọng. Giọng đồng hành, khích lệ, gọn."

- **AI Engineer** — `data_plan`: `own_tasks`
  `description`: "Xây dựng, thử nghiệm và triển khai các hệ thống/mô hình AI
  (Machine Learning, Deep Learning), chịu trách nhiệm về kiến trúc hạ tầng dữ
  liệu và mô hình."
  `kickoff_prompt`: "Tập trung vào CÔNG VIỆC CỦA RIÊNG người dùng: điểm qua các
  task xây dựng/triển khai mô hình & hạ tầng dữ liệu đang được giao, gợi ý nên
  làm việc nào trước theo hạn/độ quan trọng. Giọng kỹ thuật, đồng hành, gọn."

- **Software Engineer** — `data_plan`: `own_tasks`
  `description`: "Lập trình, phát triển và bảo trì các ứng dụng phần mềm, hệ
  thống theo yêu cầu kỹ thuật của dự án."
  `kickoff_prompt`: "Tập trung vào CÔNG VIỆC CỦA RIÊNG người dùng: điểm qua các
  task phát triển/bảo trì đang được giao, gợi ý ưu tiên theo hạn và độ quan
  trọng. Giọng đồng hành, gọn."

- **Associate System Manager** — `data_plan`: `own_tasks`
  `description`: "Hỗ trợ quản lý, vận hành và giám sát hệ thống hạ tầng CNTT,
  đảm bảo tính ổn định, bảo mật và hiệu năng của hệ thống."
  `kickoff_prompt`: "Tập trung vào CÔNG VIỆC CỦA RIÊNG người dùng: điểm qua các
  task vận hành/giám sát hệ thống đang được giao, lưu ý việc gấp hoặc ảnh hưởng
  ổn định hệ thống trước. Giọng cẩn trọng, ưu tiên việc khẩn."

- **Lead System Engineer** — `data_plan`: `cross_project`
  `description`: "Trưởng nhóm kỹ sư hệ thống, chịu trách nhiệm thiết kế kiến
  trúc hạ tầng lớn, dẫn dắt đội ngũ kỹ thuật và giải quyết các sự cố hệ thống
  phức tạp."
  `kickoff_prompt`: "Cho người dùng cái nhìn TỔNG QUAN các project hệ thống họ
  phụ trách: task mới, sự cố đang mở, và mời rà soát phân công cho đội. Giọng
  tổng hợp, ưu tiên rủi ro và bức tranh toàn cảnh hơn chi tiết."

- **Business Analyst** — `data_plan`: `cross_project`
  `description`: "Phân tích yêu cầu nghiệp vụ từ khách hàng hoặc các bên liên
  quan, chuyển hóa thành tài liệu kỹ thuật để đội ngũ phát triển phần mềm thực
  hiện."
  `kickoff_prompt`: "Cho người dùng cái nhìn TỔNG QUAN nhiều project họ liên
  quan: số task mới, project nào vừa có thay đổi, và mời họ rà soát. Giọng tổng
  hợp, súc tích, ưu tiên bức tranh toàn cảnh hơn chi tiết từng task."

- **Lead QC Engineer** — `data_plan`: `cross_project`
  `description`: "Trưởng nhóm kiểm thử chất lượng phần mềm, lên kế hoạch kiểm
  thử (test plan), giám sát quy trình QC và đảm bảo chất lượng đầu ra của sản
  phẩm."
  `kickoff_prompt`: "Cho người dùng cái nhìn TỔNG QUAN chất lượng across project:
  task kiểm thử/bug đang mở, hạng mục chờ QC, và mời rà soát kế hoạch test. Giọng
  tổng hợp, ưu tiên rủi ro chất lượng và việc đang nghẽn."

#### Dept: Product

- **Lead Software Engineer** — `data_plan`: `cross_project`
  `description`: "Trưởng nhóm lập trình phần mềm, chịu trách nhiệm chính về kiến
  trúc mã nguồn, định hướng kỹ thuật cho dự án và quản lý năng suất của các kỹ sư
  phần mềm."
  `kickoff_prompt`: "Cho người dùng cái nhìn TỔNG QUAN các project họ dẫn dắt:
  task mới, việc của đội đang nghẽn, mời rà soát phân công/kiến trúc. Giọng tổng
  hợp, ưu tiên điểm nghẽn của đội."

- **Associate Product Growth Executive** — `data_plan`: `cross_project`
  _(⚠️ borderline — flip to `own_tasks` if this role works off a personal task
  queue rather than a product-wide view)_
  `description`: "Chuyên viên hỗ trợ tăng trưởng sản phẩm, tham gia vào việc
  phân tích dữ liệu người dùng, tối ưu hóa trải nghiệm và thực hiện các chiến
  dịch thúc đẩy người dùng sử dụng sản phẩm."
  `kickoff_prompt`: "Cho người dùng cái nhìn TỔNG QUAN các hạng mục sản
  phẩm/tăng trưởng họ liên quan: task mới, chiến dịch/thử nghiệm đang chạy, mời
  rà soát ưu tiên. Giọng tổng hợp, hướng dữ liệu, súc tích."

#### Dept: GreenNode HR & Admin

- **L&D Executive** — `data_plan`: `minimal`
  _(⚠️ borderline — flip to `own_tasks` if L&D work is tracked in Redmine)_
  `description`: "Chuyên viên Đào tạo và Phát triển (Learning & Development),
  chịu trách nhiệm xây dựng lộ trình học tập, tổ chức các khóa đào tạo nâng cao
  kỹ năng và phát triển năng lực cho nhân sự."
  `kickoff_prompt`: "Chào ngắn gọn theo vai trò L&D, giới thiệu Mee là trợ lý
  cuộc họp và mời người dùng hỏi hoặc giao việc (không bịa số liệu task)."

#### Default fallback (no role / unknown role)

- **(default)** — `data_plan`: `minimal`
  `kickoff_prompt`: "Chào ngắn gọn, giới thiệu Mee là trợ lý cuộc họp và mời
  người dùng hỏi hoặc giao việc."
