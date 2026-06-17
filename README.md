# Mee — Meeting Note Agent

> AI meeting agent cho tiếng Việt: **paste / upload audio / live record** → transcript → **WYSIWYG Clean editor** → **Biên bản phiên họp (MoM)** + **Tổng kết project**. Powered by LangGraph + Qwen3 LLM + PhoWhisper STT.

---

## ✨ Tính năng chính

| Feature | Mô tả |
|---|---|
| **Project / Phiên họp 2 cấp** | Project (folder) chứa N phiên họp. Mỗi phiên = 1 transcript + 1 biên bản riêng |
| **3 input modes** | Paste text · Upload audio (mp3/wav/m4a, auto-chunk >24MB) · Live record (mic → WebSocket → Whisper streaming) |
| **MoM per-recording** | Biên bản sinh cho **1 phiên cụ thể**, lưu `recordings.mom_json`. Ưu tiên transcript đã edit (Clean) làm input |
| **Project summary** | Tổng kết toàn project = timeline quyết định theo thời gian + narrative LLM (aggregate N MoM) |
| **TipTap WYSIWYG Clean editor** | Edit transcript inline (bold/italic/lists/headings) + tag chips (commitment/decision/blocker) + auto-save 1.5s |
| **Self-hosted PhoWhisper + pyannote** | Tiếng Việt 8.85% WER + speaker diarization (deploy L40 GPU), hoặc dùng VNG MaaS Whisper |
| **Hybrid memory** | Cross-meeting: keyword tsvector + semantic bge-m3 (1024-d pgvector) + RRF fusion + optional LLM rerank |
| **Ghi nhớ hội thoại** | `remember_fact`/`forget_fact`: agent lưu sự thật user nêu trong chat vào AgentBase, recall vào prompt lượt sau |
| **Chat HITL + Redmine** | Chat agent với Human-in-the-Loop, thao tác Redmine trực tiếp qua MCP (tool ghi cần duyệt) |
| **i18n VI/EN + theme** | UI 2 ngôn ngữ, dark/light (persist localStorage, default dark) |

---

## 🚀 Cài đặt & chạy

### Yêu cầu

- Python ≥ 3.11 · Node.js ≥ 18 · Postgres ≥ 14 **với pgvector extension** (remote hoặc local docker)
- VNG Cloud MaaS API key **HOẶC** self-hosted Qwen3 + bge-m3 + Whisper

### 1. Backend (Python)

```bash
git clone <repo-url> && cd mee-meeting-agent

python -m venv venv                  # tên venv chuẩn của repo là `venv` (KHÔNG phải .venv)
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
venv/bin/pip install "psycopg[binary]"   # BẮT BUỘC cho LangGraph checkpointer; thiếu → "libpq not found"
```

> Đừng commit `venv/` (đã gitignore). Nếu `psycopg[binary]` không có wheel cho máy bạn:
> `sudo apt-get install -y libpq5` (Debian/Ubuntu) hoặc `brew install libpq` (macOS).

### 2. Cấu hình `.env`

```bash
cp .env.example .env      # rồi điền giá trị thật
```

Các biến tối thiểu để chạy:

```env
# LLM (cleaner + MoM + chat) — self-hosted Qwen3 (vLLM) hoặc VNG MaaS
LLM_BASE_URL=http://<llm-host>:8000/v1
LLM_API_KEY=EMPTY
LLM_MODEL=Qwen/Qwen3-8B

# Whisper (STT) — VNG MaaS hoặc self-hosted PhoWhisper (xem tools/phowhisper-server/)
WHISPER_BASE_URL=https://<maas-host>/.../whisper-large-v3
WHISPER_API_KEY=vn-...
WHISPER_MODEL=openai/whisper-large-v3

# Embedding — bge-m3 1024-d. LƯU Ý: code đọc tên EMBED_* (KHÔNG phải EMBEDDING_*)
EMBED_BASE_URL=https://<maas-host>/.../bge-m3/v1
EMBED_API_KEY=vn-...
EMBED_MODEL=BAAI/bge-m3
EMBED_DIM=1024

# Database — phải bật pgvector. Code tự thêm driver prefix (+asyncpg / +psycopg2)
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

> dotenv load với `interpolate=False` nên password chứa `$` được giữ nguyên.
> Các biến tùy chọn (O365 login, Redmine MCP, AgentBase memory, tracing) xem comment trong `.env.example`.

### 3. DB migrations

```bash
# Local Postgres+pgvector (bỏ qua nếu dùng DATABASE_URL remote):
docker compose --profile local up -d        # postgres:5435, adminer:8080

venv/bin/alembic upgrade head                # tạo schema + pgvector + IVFFlat index
```

### 4. Chạy backend + frontend

```bash
# Terminal 1 — backend (HTTP :8002 + WebSocket :9091)
venv/bin/python run_meeting.py

# Terminal 2 — React frontend (Vite :8001, proxy /api+/auth→:8002, /ws→:9091)
cd meeting_frontend_react
npm install                                  # lần đầu, hoặc khi package.json đổi
npm run dev
```

Mở **http://localhost:8001** (Vite chạy :8001 vì đó là host đã đăng ký OAuth callback trên Azure).
Build production: `npm run build` → `dist/`.

> Backend phải chạy trước (`:8002`) thì proxy `/api` mới hoạt động. Lỗi peer-deps khi `npm install` → thử `--legacy-peer-deps`.
> Legacy vanilla frontend vẫn served bởi FastAPI tại `http://localhost:8002/` (fallback).

---

## 🧭 Workflow

```
[Paste text / Upload audio / Live record]
        │   (audio → POST /api/transcribe hoặc WSS :9091 → Whisper)
        ▼
   Raw transcript ──/import-transcript──▶ transcript_segments
        │
        ├─ (optional) Tab "Clean" → TipTap editor (LLM clean + user edit) → clean_segments.edited_text
        ▼
   "Biên bản phiên này"  POST /recordings/{id}/generate-mom
        ▼
   MomGraph: load_transcript(edited > raw) → read_memory(hybrid) → generate_mom(Qwen3 map-reduce) → save_results
        ▼
   recordings.mom_json  +  memory_events(+bge-m3 vector)  +  output/MoM_*.md  →  MoMPane

   "Tổng kết project"  /generate-project-summary
        ▼
   project_summarizer aggregate mom_json theo started_at → meetings.project_summary_json
```

---

## 🖱 Cách dùng

1. Sidebar → **"+ Project"** → tự tạo "Phiên 1" → vào workspace.
2. Chọn input: **Paste** (dán text) · **Upload** (chọn audio) · **Record** (cho phép mic → Dừng).
3. (Optional) Tab **"Clean"** → LLM clean → edit inline + bôi đen gắn tag → auto-save 1.5s (hoặc Ctrl+S).
4. **"Biên bản phiên này"** → MoM hiện ở MoMPane.
5. Thêm phiên: **"+ Phiên họp mới"** → lặp lại.
6. **"Tổng kết project"** (click project, không click phiên) → timeline decisions + narrative.
7. Quản lý: hover project → ⋮ (Share/Pin/Rename/Delete) · hover phiên → × · gear icon → theme + ngôn ngữ.

---

## 🏗 Kiến trúc

```mermaid
flowchart TB
    subgraph FE["🖥 Frontend"]
        FE1[React 18 + TS + Vite + TipTap]
        FE2[Legacy vanilla JS<br/>meeting_frontend/]
    end
    subgraph BE["⚙️ FastAPI Backend"]
        API[API: meetings.py · chat.py]
        SVC[Services: memory · cleaner · summarizer · embedding · reranker]
        LG[LangGraph: MomGraph · ChatGraph<br/>AsyncPostgresSaver]
    end
    subgraph EXT["☁️ AI services (OpenAI-compatible)"]
        STT[STT: PhoWhisper+pyannote L40<br/>OR VNG MaaS]
        LLM[LLM: Qwen3 vLLM OR VNG MaaS]
        EMB[bge-m3 embedding]
    end
    PG[("🗄 Postgres + pgvector")]
    FE -->|HTTP REST + WebSocket| BE
    API --> SVC --> PG
    API --> LG --> PG
    LG --> SVC
    BE --> STT
    SVC --> EMB
    SVC --> LLM
    LG --> LLM
    style FE fill:#d0ebff,stroke:#1971c2
    style BE fill:#fff3bf,stroke:#e67700
    style EXT fill:#ffe8cc,stroke:#fd7e14
    style PG fill:#e7f5ff,stroke:#1971c2
```

### DB schema

| Cấp | Table | Description |
|---|---|---|
| 1 | `meetings` | Project — title, attendees, is_pinned, project_summary_json |
| 2 | `recordings` | Phiên họp — session_label, started_at, mom_json, clean_segments |
| 3 | `transcript_segments` | Raw segments per câu — seq, original_text, edited_text |
| Side | `users` · `meeting_members` · `memory_events` (+ embedding vector(1024)) | Auth + sharing + memory |
| Chat | `chat_sessions` · `chat_messages` · `pending_actions` · `audit_log` | Chat HITL |
| LangGraph | `checkpoints` · `checkpoint_writes` · `checkpoint_blobs` | Resume state (thread_id = recording_id) |

> Migrations `0001`…`0023` trong `alembic/versions/`. Schema tiến hóa qua: pgvector/IVFFlat (`0006`), MoM 2 cấp (`0007`), chat user-scoped (`0022`), recording comments (`0023`), …

---

## 🔗 Chat agent ↔ Redmine (MCP)

Chat agent thao tác Redmine **trực tiếp qua MCP server** (`MCP_REDMINE_URL`, Bearer token = Redmine API key).
Tool được **khám phá động** (`list_tools()` lúc khởi động + disk-cache `.mcp_redmine_tools_cache.json`); tool **ghi**
(`create_redmine_issue`/`update_redmine_issue`/`bulk_update_issues`) bị chặn bởi HITL — user phải duyệt trước khi chạy.
pm-agent (A2A) chỉ chạy khi user gõ tiền tố `/pm-agent` (định tuyến tất định).

**Đồng bộ MoM → Redmine (`create_task`):** agent dựng danh sách việc từ MoM, hỏi **một lần duyệt** cho cả lô, rồi áp
qua MCP trong node `agent_execute` (mỗi việc → `create_redmine_issue`, hoặc `update_redmine_issue` nếu có `issue_id`).
`due_date` được chuẩn hóa về `YYYY-MM-DD` trước khi gửi (giá trị không hợp lệ bị bỏ).

> Env: `MCP_REDMINE_URL`, `REDMINE_API_KEY` (hoặc per-user key qua AgentBase Identity). Dep client: `mcp>=1.25.0`.
> Probe: `venv/bin/python scripts/probe_redmine_mcp.py`.

---

## 🧠 Ghi nhớ hội thoại — remember_fact / forget_fact

Agent lưu kiến thức nêu trong chat thành **bộ nhớ lâu dài** trong **AgentBase memory-records** (không phải `memory_events`),
vượt qua "Xóa hội thoại". Hai scope:

| scope | namespace | dùng cho |
|---|---|---|
| `user` | `user_prefs/<ms_oid>` | sự thật riêng user (danh xưng, sở thích) |
| `project` | `project_facts/<meeting_id>` | sự thật chia sẻ trong dự án (theo `meeting_id`) |

- 2 tool tự chạy (không cần duyệt), ghi **chạy ngầm** (fire-and-forget). Thiếu `MEMORY_ID` → no-op.
- AgentBase **insert-only** (DELETE 403): `forget_fact` ghi tombstone (`active=0`); recall newest-wins theo `key`, ẩn tombstone.
- `load_context` nạp user-facts + project-facts của phiên hiện tại vào khối **"Ghi nhớ"** trong prompt (20 mục mới nhất).

> Inspect: `venv/bin/python scripts/dump_agent_memory.py <meeting_id> <ms_oid>` (in view hiệu lực — đã ẩn tombstone).
> Spec: `docs/superpowers/specs/2026-06-16-chat-knowledge-capture-design.md`.

---

## 📂 Cấu trúc thư mục

```
mee-meeting-agent/
├── meeting/                      # Backend Python package
│   ├── api/                      # meetings.py (REST) · chat.py (HITL)
│   ├── db/                       # base.py (engine) · models.py (ORM) · repositories.py
│   ├── graphs/                   # mom_graph.py · chat_graph.py · checkpointer.py
│   ├── services/                 # memory · transcript_cleaner · project_summarizer · embedding · reranker
│   ├── app.py                    # FastAPI factory
│   ├── note_generator.py         # MoM LLM (map-reduce + Qwen3 think-strip)
│   └── report_generator.py       # MoM JSON → Markdown
├── meeting_frontend_react/       # React frontend (recommended) — Vite + React 18 + TS + TipTap
│   └── src/                      # api/client.ts · store/AppContext.tsx · hooks/ · i18n.ts · components/
├── meeting_frontend/             # Legacy vanilla SPA (fallback, served bởi FastAPI)
├── whisper_live/                 # Whisper streaming backend (maas_backend.py)
├── tools/phowhisper-server/      # Self-hosted PhoWhisper + pyannote (deploy L40)
├── alembic/versions/             # DB migrations 0001–0023
├── scripts/                      # backfill_embeddings · dump_agent_memory · probe_* …
├── run_meeting.py                # Main entry (HTTP :8002 + WebSocket :9091)
├── docker-compose.yml            # Local Postgres+pgvector (profile=local)
├── requirements.txt · .env.example · README.md
```

---

## 🌐 Agent runtime (GreenNode AgentBase)

Agent được deploy trên **GreenNode AgentBase runtime**. Endpoint (docs / playground):

> https://endpoint-e2c26683-c6aa-4f05-8502-57eec4d78c35.agentbase-runtime.aiplatform.vngcloud.vn

> Credentials runtime + container registry nằm trong `.greennode.json` (gitignored — KHÔNG commit).

---

## 🤝 License

Internal project — VNG Cloud / GreenNode AI team.

**Built with**: FastAPI · SQLAlchemy 2 async · LangGraph · pgvector · openai SDK · React 18 · TypeScript · Vite · TipTap · Qwen3 · bge-m3 · PhoWhisper-large · pyannote 3.1
