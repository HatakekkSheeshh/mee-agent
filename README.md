# Mee — Meeting Note Agent

> AI meeting agent cho tiếng Việt: **paste / upload audio / live record** → transcript → **WYSIWYG Clean editor** → **Biên bản phiên họp (MoM)** + **Tổng kết project**. Powered by LangGraph + Qwen3 LLM + PhoWhisper STT.

---

## ✨ Tính năng chính

| Feature | Mô tả |
|---|---|
| **Project / Phiên họp 2 cấp** | Project (folder) chứa N phiên họp. Mỗi phiên = 1 transcript + 1 biên bản riêng |
| **3 input modes** | Paste text · Upload audio (mp3/wav/m4a, auto-chunk >24MB) · Live record (mic + WebSocket → Whisper streaming) |
| **MoM per-recording** | Biên bản sinh ra cho **1 phiên cụ thể** (không phải toàn project). Lưu `recordings.mom_json` |
| **Project summary** | Tổng kết toàn project = timeline các quyết định theo thời gian + narrative LLM (aggregate từ N MoM của các phiên) |
| **TipTap WYSIWYG Clean editor** | User edit transcript inline với bold/italic/lists/headings + tag chips (commitment/decision/blocker) + auto-save 1.5s |
| **MoM dùng edited Clean** | Khi tạo MoM, ưu tiên transcript đã edit (cleaner input → quality cao hơn) |
| **Self-hosted PhoWhisper + pyannote** | Tiếng Việt 8.85% WER (BSD-3) + speaker diarization, deploy trên L40 GPU |
| **Hybrid memory** | Cross-meeting memory: keyword tsvector + semantic bge-m3 (1024-dim pgvector) + RRF fusion, optional LLM rerank |
| **Chat HITL** | Chat agent với Human-in-the-Loop: classify intent → propose action → user approve/reject → execute |
| **Sidebar context menu** | Hover project → ⋮ → Share / Rename / Pin / Delete. Hover phiên → × delete |
| **i18n VI/EN** | Toàn bộ UI có 2 ngôn ngữ, switch trong settings |
| **Theme dark/light** | Persist localStorage. Default = dark (GreenNode aesthetic) |

---

## 🔄 Workflow

### Luồng chính: Tạo Biên bản

```mermaid
flowchart TD
    A["👤 Click 'Project mới' → tạo Phiên 1"] -->|POST /api/meetings + /recordings| B[(meetings + recordings)]
    B --> C{Choose input}

    C --> D1[📝 Paste text]
    C --> D2[📁 Upload audio]
    C --> D3[🎤 Live record]

    D2 -->|POST /api/transcribe| W{{Whisper API<br/>PhoWhisper L40 / VNG MaaS}}
    D3 -->|WSS :9091| W

    D1 --> T[Raw transcript]
    W --> T
    T -->|/import-transcript<br/>recording_id| RS[(transcript_segments)]

    RS --> CE{Optional}
    CE -->|Click 'Clean' tab| TT[🪶 TipTap Editor<br/>LLM clean + user edit]
    TT -->|PATCH /clean-edited| EDT[(clean_segments.<br/>edited_html<br/>edited_text)]

    RS --> GM
    EDT -->|preferred| GM[Click 'Biên bản phiên này']

    GM -->|"POST /recordings/{id}/generate-mom"| LG[🧠 MomGraph 4 nodes]
    LG --> N1[load_transcript<br/>edited_text > raw]
    N1 --> N2[read_memory<br/>hybrid retrieval]
    N2 --> N3[generate_mom<br/>Qwen3-8B map-reduce]
    N3 --> N4[save_results]
    N4 --> O1[(recordings.mom_json)]
    N4 --> O2[(memory_events<br/>+ bge-m3 vector)]
    N4 --> O3[output/MoM_*.md]

    O1 --> UI[📋 MoM Pane render]

    PS["📊 Click 'Tổng kết project'"] -->|/generate-project-summary| PSG[project_summarizer]
    PSG -->|aggregate all recording.mom_json by started_at ASC| PS2[(meetings.project_summary_json)]
    PS2 --> UI

    style W fill:#ffe8cc,stroke:#fd7e14
    style LG fill:#e5dbff,stroke:#862e9c
    style TT fill:#fff3bf,stroke:#e67700
    style UI fill:#d3f9d8,stroke:#2f9e44
```

### Output ở đâu?

| Loại | Vị trí | Mô tả |
|---|---|---|
| **Biên bản phiên (MoM)** | `recordings.mom_json` (JSONB) | Per-recording. Render qua MoMPane + download `.md` |
| **Tổng kết project** | `meetings.project_summary_json` (JSONB) | Timeline decisions + narrative |
| **Clean transcript edited** | `recordings.clean_segments.edited_html` + `.edited_text` | TipTap output, dùng làm input cho MoM nếu có |
| **Clean transcript LLM-output** | `recordings.clean_segments.segments[]` | Speaker blocks + tags (raw LLM) |
| **Memory events** | `memory_events` rows + `embedding` vector(1024) | Cross-meeting context cho MoM sau |
| **LangGraph checkpoints** | `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` | Resume nếu fail giữa chừng (thread_id = recording_id) |
| **MoM markdown** | `./output/MoM_<label>_<id>.md` | Local file backup |

---

## Cài đặt & chạy

### 1. Yêu cầu

- Python ≥ 3.11
- Node.js ≥ 18 (cho React frontend)
- Postgres ≥ 14 **với pgvector extension** (remote VDB hoặc local docker)
- Browser hiện đại (Chrome/Firefox/Edge)
- VNG Cloud MaaS API key HOẶC self-hosted Qwen3 + bge-m3

### 2. Clone & install backend (Python libs)

```bash
git clone <repo-url>
cd mee-meeting-agent

python -m venv venv                       # tên venv chuẩn của repo là `venv` (KHÔNG phải .venv)
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# BẮT BUỘC: psycopg3 binary cho LangGraph checkpointer (Postgres).
# Thiếu bước này → server crash: "ImportError: no pq wrapper available / libpq not found".
venv/bin/pip install "psycopg[binary]"
```

> **Lưu ý venv:** mọi lệnh trong README này dùng `venv/bin/...`. Một số script/tài liệu cũ ghi `.venv` —
> repo đang dùng `venv`. Đừng commit thư mục `venv/` (đã gitignore; commit nhầm → push 50MB → lỗi HTTP 413).
>
> **Nếu `psycopg[binary]` không có wheel cho máy bạn**, cài libpq hệ thống thay thế:
> `sudo apt-get install -y libpq5` (Debian/Ubuntu) hoặc `brew install libpq` (macOS).

### 3. Cấu hình `.env`

```bash
cp .env.example .env
nano .env
```

Biến cần thiết:

```env
# Whisper (STT) — chọn 1
# Option A: VNG MaaS
WHISPER_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/maas/user-<id>/openai/whisper-large-v3
WHISPER_API_KEY=vn-...
WHISPER_MODEL=openai/whisper-large-v3
# Option B: self-hosted PhoWhisper + pyannote (xem tools/phowhisper-server/README.md)
# WHISPER_BASE_URL=http://<L40_IP>:9100/v1
# WHISPER_MODEL=phowhisper

# LLM — self-hosted Qwen3 (vLLM) hoặc VNG MaaS
LLM_BASE_URL=http://<your-llm-host>:8000/v1
LLM_API_KEY="EMPTY"
LLM_MODEL=Qwen/Qwen3-8B

# Embedding — bge-m3 (1024-dim, qua VNG MaaS)
EMBEDDING_BASE_URL=https://maas-embedding-aiplatform-hcm.api.vngcloud.vn/maas/user-<id>/bge-m3/v1
EMBEDDING_API_KEY=vn-...
EMBEDDING_MODEL=BAAI/bge-m3

# Database — phải bật pgvector
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

> Code tự thêm driver prefix (`+asyncpg` / `+psycopg2`). Password có `$` được preserve (dotenv `interpolate=False`).

### 4. Apply DB migrations

```bash
.venv/bin/alembic upgrade head
```

Tạo 13+ tables: `users`, `meetings`, `recordings`, `transcript_segments`, `meeting_members`, `memory_events` (với `embedding` vector(1024) + IVFFlat index), `chat_*`, LangGraph internal tables.

Migrations:
- `0001_initial_schema` — core tables
- `0002_chat_tables` — chat sessions/messages/pending_actions
- `0003_memory_events` — cross-meeting memory
- `0004_meeting_pin` — `meetings.is_pinned`
- `0005_clean_cache` — `recordings.clean_segments` JSONB
- `0006_memory_embedding` — pgvector + IVFFlat
- `0007_mom_two_level` — `recordings.mom_json` + `meetings.project_summary_json`

### 5. Run backend

```bash
.venv/bin/python run_meeting.py
```

Server khởi động:
- **HTTP API** ở `http://localhost:8001`
- **WebSocket transcription** ở `ws://localhost:9091`

### 6. Run React frontend — UI (cài package Node)

Yêu cầu **Node.js ≥ 18** (kiểm tra: `node -v`). Lần đầu phải `npm install` để tải toàn bộ
package UI (React 18, Vite, TipTap, …) vào `node_modules/` — chỉ cần chạy lại khi `package.json` đổi.

```bash
cd meeting_frontend_react
npm install        # cài dependencies UI (lần đầu, hoặc khi package.json thay đổi)
npm run dev        # chạy Vite dev server
```

Vite dev server ở `http://localhost:5173`. Proxy `/api` → backend `:8001`, `/ws` → `:9091`.

Build cho production: `npm run build` → `dist/`.

> **Lỗi npm thường gặp:** nếu `npm install` báo xung đột peer-deps → thử `npm install --legacy-peer-deps`.
> Đừng commit `node_modules/` (đã gitignore). Backend phải chạy trước (`:8001`) thì proxy `/api` mới hoạt động.

Legacy vanilla frontend vẫn ở `meeting_frontend/`, served bởi FastAPI tại `http://localhost:8001/`.

### 7. (Optional) Self-host PhoWhisper + pyannote

Nếu muốn STT chất lượng cao tiếng Việt + speaker diarization:

```bash
cd tools/phowhisper-server
# Đọc README.md để deploy lên L40
```

Server expose `/v1/audio/transcriptions` OpenAI-compatible. Update `WHISPER_BASE_URL` trong `.env`.

---

## Cách dùng (User flow)

### A. Tạo project + phiên đầu tiên

1. Sidebar trái → click **"+ Project"** → nhập tên
2. Auto-tạo "Phiên 1" trong project → vào workspace
3. Title field hiển thị tên phiên (có thể đổi bằng cách click + gõ + blur → tự save)
4. Chọn input mode:
   - **Paste**: dán text vào textarea Raw
   - **Upload**: click "Tải lên" → chọn audio
   - **Record**: click "Ghi âm" → cho phép mic → "Dừng" khi xong
5. Click **"Biên bản phiên này"** → MoM hiện ở MoMPane phải

### B. Thêm phiên vào project có sẵn

1. Click project trong sidebar (nếu >1 phiên, hiện overview với cards + project summary bên phải)
2. Click **"+ Phiên họp mới"** trong sidebar → tự tạo "Phiên N+1"
3. Lặp flow A từ bước 4

### C. Edit Clean transcript (TipTap WYSIWYG)

1. Trong workspace của 1 phiên → tab **"Clean"**
2. Lần đầu: LLM clean → render trong editor
3. Edit thẳng: bold/italic/lists, hoặc bôi đen + click tag **Commit / Decision / Blocker** để highlight
4. Auto-save sau 1.5s không gõ (hoặc Ctrl+S force)
5. Khi click "Biên bản phiên này", LLM sẽ dùng phiên bản đã edit làm input

### D. Tổng kết project

1. Project có ≥1 phiên đã có MoM → click vào project (không click phiên cụ thể)
2. Vào ProjectOverview mode → MoMPane phải hiển thị empty summary
3. Click **"Tổng kết project"** ở MoMPane header
4. Hiện timeline decisions theo `started_at` của từng phiên + narrative

### E. Quản lý

- **Hover project** → ⋮ menu: Share / Pin / Rename / Delete (red)
- **Hover phiên** → × delete
- **Settings (gear icon)** → Theme (light/dark) + Language (VI/EN)
- **Chat toggle (góc phải)** → mở/đóng chat panel (HITL chat agent)
- **Sidebar toggle (≡)** → collapse sidebar

---

## 🏗 Kiến trúc

```mermaid
flowchart TB
    subgraph FE["🖥 Frontend"]
        FE1[React 18 + TS + Vite<br/>+ TipTap editor]
        FE2[Legacy vanilla JS<br/>meeting_frontend/]
    end

    subgraph BE["⚙️ FastAPI Backend"]
        API[API endpoints<br/>meetings.py · chat.py]
        SVC[Services<br/>memory · cleaner · summarizer · embedding · reranker]
        LG[LangGraph<br/>MomGraph · ChatGraph<br/>AsyncPostgresSaver]
        WSP[WhisperLive streaming<br/>maas_backend.py]
    end

    subgraph EXT["☁️ AI services"]
        STT[STT cluster L40<br/>PhoWhisper + pyannote<br/>OR VNG MaaS Whisper]
        LLM[LLM<br/>Qwen3-8B self-hosted vLLM<br/>OR VNG MaaS]
        EMB[bge-m3 embedding<br/>VNG MaaS]
    end

    PG[("🗄 Postgres + pgvector<br/>13 tables")]

    FE -->|HTTP REST<br/>WebSocket| BE
    API --> SVC
    API --> LG
    LG --> SVC
    SVC --> PG
    LG --> PG
    WSP --> STT
    SVC --> EMB
    SVC --> LLM
    LG --> LLM

    style FE fill:#d0ebff,stroke:#1971c2
    style BE fill:#fff3bf,stroke:#e67700
    style EXT fill:#ffe8cc,stroke:#fd7e14
    style PG fill:#e7f5ff,stroke:#1971c2
```

### DB Schema (13 tables)

| Cấp | Table | Description |
|---|---|---|
| 1 | `meetings` | Project — title, attendees, is_pinned, project_summary_json |
| 2 | `recordings` | Phiên họp — session_label, started_at, mom_json, clean_segments |
| 3 | `transcript_segments` | Raw segments per câu — seq, original_text, edited_text |
| Side | `users` · `meeting_members` · `memory_events` (+ embedding) | Auth + sharing + cross-meeting memory |
| Chat | `chat_sessions` · `chat_messages` · `pending_actions` · `audit_log` | Chat HITL |
| LangGraph | `checkpoints` · `checkpoint_writes` · `checkpoint_blobs` | Resume state |

---

## 🔗 Chat agent ↔ Redmine (MCP)

Chat agent của Mee thao tác Redmine **trực tiếp qua MCP server** đã deploy
(`MCP_REDMINE_URL`, xác thực bằng header `Authorization: Bearer <REDMINE_API_KEY>` —
token Bearer **chính là** Redmine API key). Các tool được **khám phá động**
(`list_tools()` lúc khởi động, có disk-cache) và đăng ký vào tool registry; tool
**ghi** bị chặn bởi HITL (user phải duyệt trước khi chạy). pm-agent (A2A) bị **hạ
xuống opt-in**: chỉ chạy khi user gõ tiền tố lệnh `/pm-agent` (định tuyến tất định,
không qua LLM).

**Env bắt buộc:** `MCP_REDMINE_URL`, `REDMINE_API_KEY`. **Dep:** `mcp>=1.25.0`
(client; KHÔNG cần `fastmcp`/`python-redmine` — đó là phía server). Dùng
`scripts/probe_redmine_mcp.py` để kiểm tra auth/transport + in schema thực tế.

**15 tool Redmine hiện có (12 đọc / 3 ghi):**

| Tool | Loại | Công dụng |
|------|------|-----------|
| `get_redmine_projects` | đọc | Danh sách project có quyền truy cập |
| `get_field_metadata` | đọc | Giá trị hợp lệ của status/priority/tracker/assignee (để map đúng tên) |
| `list_redmine_issue` | đọc | Liệt kê issue trong một project |
| `get_redmine_issue_by_id` | đọc | Một issue theo id (kèm project + trạng thái) |
| `get_issue_children_recursive` | đọc | Toàn bộ subtask (đệ quy) của một issue |
| `get_project_updates` | đọc | Issue được cập nhật trong khoảng ngày |
| `get_stale_issues` | đọc | Issue không cập nhật quá N ngày |
| `get_overdue_issues` | đọc | Issue mở đã quá hạn |
| `get_issues_due_soon` | đọc | Issue mở đến hạn trong N ngày tới |
| `get_version_progress` | đọc | Tiến độ một target version |
| `get_workload_by_assignee` | đọc | Số issue mở theo từng người |
| `get_unassigned_issues` | đọc | Issue mở chưa giao cho ai |
| `create_redmine_issue` | **ghi** | Tạo một issue (HITL — cần duyệt) |
| `update_redmine_issue` | **ghi** | Cập nhật issue theo id (HITL — cần duyệt) |
| `bulk_update_issues` | **ghi** | Cập nhật hàng loạt issue (HITL — cần duyệt) |

> Tool được khám phá động nên danh sách có thể đổi theo bản deploy của MCP server.
> Phân loại ghi/đọc dựa trên tập tên ghi tường minh + tiền tố động từ
> (`create`/`update`/`delete`/`bulk`…), mặc định an toàn (động từ lạ → coi là ghi → cần duyệt).

**Đồng bộ biên bản → Redmine (`create_task`):** khi user yêu cầu "đồng bộ việc
trong cuộc họp lên Redmine", agent gọi tool `create_task`. Hệ thống dựng danh
sách việc từ MoM (lọc theo người/ phiên nếu được chỉ định), hỏi **một lần duyệt**
cho cả lô, rồi **áp trực tiếp qua MCP** trong node `agent_execute`: mỗi việc →
`create_redmine_issue` (hoặc `update_redmine_issue` nếu việc có `issue_id`), với
`due_date` truyền thẳng là field thật. Lượt chat kết thúc bằng bản tổng kết
"đồng bộ N/M việc". Đây là đường mặc định — KHÔNG còn bắc cầu qua pm-agent
(pm-agent chỉ chạy khi gõ `/pm-agent`).

---

## 📂 Cấu trúc thư mục

```
mee-meeting-agent/
├── meeting/                          # Backend Python package
│   ├── api/
│   │   ├── meetings.py               # REST endpoints (meetings + recordings + MoM + summary)
│   │   └── chat.py                   # Chat HITL endpoints
│   ├── db/
│   │   ├── base.py                   # SQLAlchemy engine + session
│   │   ├── models.py                 # ORM (meetings, recordings, segments, memory_events, ...)
│   │   └── repositories.py           # SQL queries
│   ├── graphs/
│   │   ├── mom_graph.py              # MoM LangGraph (per-recording, prefers edited Clean)
│   │   ├── chat_graph.py             # Chat LangGraph (HITL)
│   │   └── checkpointer.py           # AsyncPostgresSaver
│   ├── services/
│   │   ├── memory_service.py         # Hybrid keyword + semantic + RRF retrieval
│   │   ├── transcript_cleaner.py     # Clean view LLM
│   │   ├── project_summarizer.py     # Aggregate per-recording MoMs → timeline summary
│   │   ├── embedding.py              # bge-m3 client
│   │   └── reranker.py               # Optional LLM rerank
│   ├── app.py                        # FastAPI factory
│   ├── note_generator.py             # MoM LLM (map-reduce + Qwen3 think-strip)
│   └── report_generator.py           # MoM JSON → Markdown
│
├── meeting_frontend_react/           # New React frontend (recommended)
│   ├── package.json                  # Vite + React 18 + TS + TipTap
│   ├── vite.config.ts                # Proxy /api → :8001, /ws → :9091
│   ├── public/audioprocessor.js      # AudioWorklet PCM resampler
│   └── src/
│       ├── App.tsx
│       ├── api/client.ts             # Typed fetch wrapper
│       ├── types/api.ts              # Backend response types
│       ├── store/AppContext.tsx      # Global state + theme/lang/chat/sidebar prefs
│       ├── hooks/
│       │   ├── useLiveRecording.ts   # WebSocket Whisper streaming
│       │   └── useResizer.ts         # Drag-to-resize panes
│       ├── i18n.ts                   # vi/en strings (60+ keys)
│       └── components/
│           ├── Sidebar.tsx           # Projects tree + ⋮ menu + × delete
│           ├── Topbar.tsx            # Brand + workspace + settings + avatar
│           ├── MeetingControl.tsx    # Title input + meta + details panel
│           ├── Workspace.tsx         # 3-pane layout + resizers
│           ├── TranscriptPane.tsx    # Raw + Clean toggle + Record/Upload/Save
│           ├── CleanEditor.tsx       # TipTap WYSIWYG (auto-save + tags)
│           ├── MeetingTag.ts         # Custom mark — commitment/decision/blocker
│           ├── MoMPane.tsx           # Render MoM + project summary
│           ├── ProjectOverview.tsx   # Cards view for project with multiple phiên
│           ├── ChatPane.tsx          # HITL chat
│           ├── Dropdown.tsx          # Reusable portal dropdown
│           └── ConfirmDialog.tsx     # Branded confirm modal
│
├── meeting_frontend/                 # Legacy vanilla SPA (kept as fallback)
│   ├── index.html · app.js · style.css · audioprocessor.js
│
├── whisper_live/                     # Whisper streaming backend
│   └── backend/maas_backend.py       # VNG MaaS adapter
│
├── tools/phowhisper-server/          # Self-hosted PhoWhisper + pyannote (deploy lên L40)
│   ├── server.py                     # FastAPI OpenAI-compatible STT + diarize
│   ├── requirements.txt
│   └── README.md                     # SCP + setup guide
│
├── alembic/versions/                 # DB migrations 0001-0007
│
├── scripts/backfill_embeddings.py    # One-shot: embed existing memory_events
├── output/                           # Generated MoM .md files
├── docs/                             # Internal docs + drawio diagrams
├── run_meeting.py                    # Main entry (HTTP + WebSocket)
├── docker-compose.yml                # Local Postgres+pgvector (profile=local)
├── requirements.txt
├── .env.example
└── README.md                         # This file
```

---

## Test workflow nhanh

```bash
# Terminal 1: backend
.venv/bin/python run_meeting.py

# Terminal 2: React dev (recommended)
cd meeting_frontend_react && npm run dev

# Browser: http://localhost:5173
# 1. "+ Project" → "Test"
# 2. Auto vào "Phiên 1"
# 3. Click "Tải lên" → chọn 1 file .wav/.mp3
# 4. Đợi transcribe + import
# 5. Tab "Clean" → đợi LLM → edit gì đó → auto-save sau 1.5s
# 6. "Biên bản phiên này" → MoMPane render meta + agenda + actions + decisions
# 7. "+ Phiên họp mới" → "Phiên 2" → upload audio khác → lặp lại
# 8. Click project (không click phiên) → ProjectOverview + nút "Tổng kết project"
# 9. Click "Tổng kết" → timeline decisions của cả 2 phiên
```

---

## 🤝 License

Internal project — VNG Cloud / GreenNode AI team.

---

**Built with**: FastAPI · SQLAlchemy 2 async · LangGraph · pgvector · openai SDK · React 18 · TypeScript · Vite · TipTap · Qwen3-8B · bge-m3 · PhoWhisper-large · pyannote 3.1
