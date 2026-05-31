# Mee — Meeting Note Agent

> Project (= folder) chứa nhiều phiên họp. Mỗi phiên = transcript (paste / upload audio / live record). Hệ thống dùng LangGraph + LLM để sinh **Biên bản họp (MoM)** tự động + **Clean view** dễ đọc.

---

## ✨ Tính năng chính

| Feature | Mô tả |
|---|---|
| **Project / Phiên họp** | UX kiểu ChatGPT: Project = folder, mỗi project chứa N phiên họp (recordings) |
| **3 input modes** | Paste text · Upload audio (mp3/wav/m4a) · Live record (mic + WebSocket streaming) |
| **Vietnamese-tuned MoM** | LLM extract 4 nhóm: `action_items` · `decisions` · `commitments` · `blockers` (theo VN style + few-shot examples) |
| **Clean view (cached)** | LLM format raw transcript → speaker blocks + tags, lưu cache trong DB → click lần sau instant |
| **Chat HITL** | Chat agent với Human-in-the-Loop: classify intent → propose action → user approve/reject → execute |
| **Sidebar context menu** | Hover project → ⋯ menu: Share / Rename / Pin / Delete (với DB sync) |
| **Audio device picker** | Chọn mic / loa cụ thể, lưu preference vào localStorage |

---

## 🔄 Workflow

### Luồng chính: Tạo Biên bản họp

```mermaid
flowchart TD
    A["👤 Click 'Project mới'"] -->|POST /api/meetings| B[(Postgres<br/>meeting row tạo)]
    B --> C["Vào 'Phiên họp chưa đặt tên'"]

    C --> D1[📝 Paste text]
    C --> D2[📁 Upload .mp3]
    C --> D3[🎤 Live record]

    D2 -->|POST /api/transcribe| W{{Whisper API<br/>VNG MaaS}}
    D3 -->|WSS :9091 mic stream| W

    D1 --> T[Transcript có sẵn<br/>trong textarea]
    W --> T

    T -->|Click 'Tạo biên bản'<br/>POST /generate-mom| LG

    subgraph LG[" "]
        direction TB
        LGT[🧠 LangGraph 4 nodes]
        N1[① load_transcript<br/>SELECT segments]
        N2[② read_memory<br/>top 5 events]
        N3[③ generate_mom<br/>LLM Qwen3-5-27b<br/>≤30k → 1 call<br/>>30k → map-reduce]
        N4[④ save_results]
        LGT --> N1 --> N2 --> N3 --> N4
    end

    LG --> OUT[💾 Outputs]
    OUT --> O1[(meetings.mom_json)]
    OUT --> O2[(memory_events<br/>5-15 rows)]
    OUT --> O3[output/MoM_*.md file]
    OUT --> O4[(checkpoints<br/>state snapshots)]

    OUT --> UI[🖥 Hiển thị MoM<br/>+ download .md]

    style A fill:#d0ebff,stroke:#1971c2
    style C fill:#d0ebff,stroke:#1971c2
    style T fill:#fff3bf,stroke:#e67700
    style LG fill:#e5dbff,stroke:#862e9c
    style UI fill:#d3f9d8,stroke:#2f9e44
    style W fill:#ffe8cc,stroke:#fd7e14
```

### Output ở đâu?

| Loại | Vị trí | Mô tả |
|---|---|---|
| **MoM file Markdown** | `./output/MoM_<title>_<date>_<id>.md` | Local file, có thể tải về qua UI |
| **MoM JSON** | Postgres → `meetings.mom_json` (JSONB) | Render lại trên UI, edit qua API |
| **Memory events** | Postgres → `memory_events` (rows) | Context cross-meeting cho các MoM sau |
| **Clean transcript cache** | Postgres → `recordings.clean_segments` (JSONB) | Click Clean lần sau instant (no LLM) |
| **LangGraph checkpoints** | Postgres → `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` | Resume nếu fail giữa chừng |
| **Whisper transcript backup** | `./output/transcript_<title>_<date>_<id>.txt` (legacy path) | Phòng khi LLM fail |

---

## 🚀 Cài đặt & chạy

### 1. Yêu cầu

- Python ≥ 3.11
- Postgres ≥ 14 (remote VDB hoặc local docker)
- Browser hiện đại (Chrome/Firefox/Edge)
- VNG Cloud MaaS API key (Whisper + LLM)

### 2. Clone & install

```bash
git clone <repo-url>
cd mee-meeting-agent

# Tạo virtualenv + install deps
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Cấu hình `.env`

```bash
cp .env.example .env
nano .env   # fill in real values
```

Các biến cần thiết:

```env
# Whisper (STT) — VNG Cloud MaaS
WHISPER_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/maas/user-<id>/openai/whisper-large-v3
WHISPER_API_KEY=vn-...
WHISPER_MODEL=openai/whisper-large-v3

# LLM (chat + MoM) — đổi tùy provider
LLM_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/maas/user-<id>/qwen/qwen3-5-27b/v1
LLM_API_KEY=vn-...
LLM_MODEL=qwen/qwen3-5-27b

# Database — remote VDB (recommended) hoặc local docker
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

> Code tự thêm driver prefix (`+asyncpg` cho app, `+psycopg2` cho Alembic). Password có `$` được preserve (dotenv `interpolate=False`).

### 4. Apply DB migrations

```bash
.venv/bin/alembic upgrade head
```

Tạo các tables: `users`, `meetings`, `recordings`, `transcript_segments`, `meeting_members`, `memory_events`, `chat_*`, + LangGraph internal tables.

### 5. Run server

```bash
.venv/bin/python run_meeting.py
```

Server khởi động:
- **HTTP API + FE** ở `http://localhost:8001`
- **WebSocket transcription** ở `ws://localhost:9091`

Banner sẽ log info Postgres + endpoints sẵn sàng. Mở browser tại `http://localhost:8001`.

Adminer GUI tại `http://localhost:8080`.

---

## 🎯 Cách dùng (User flow)

### A. Tạo project + phiên họp đầu tiên

1. Click **"Project mới"** (sidebar trái) → modal hỏi tên project
2. Type tên → Enter → project xuất hiện trong sidebar
3. Right panel: title field rỗng (placeholder "Phiên họp chưa đặt tên") + textarea trống
4. Chọn 1 trong 3 cách:
   - **Paste**: copy transcript text → dán vào textarea
   - **Upload**: click "Tải lên" → chọn file `.mp3/.wav/.m4a`
   - **Record**: click "Ghi âm" → cho phép mic → nói → "Dừng"
5. Type tên phiên họp vào title field
6. Click **"Tạo biên bản"** → đợi 10-60s → MoM hiện ở panel phải

### B. Thêm phiên họp vào project có sẵn

1. Click project trong sidebar
2. Nếu có 1 phiên → vào thẳng phiên đó. Nếu nhiều → list cards, click chọn
3. Click **"+ Thêm phiên họp"** → vào "Phiên họp chưa đặt tên" mới
4. Lặp lại flow A bước 4-6

### C. Quản lý project

- **Hover** project trong sidebar → ⋯ icon hiện ở góc phải
- **Click ⋯** → menu:
  - **Share project**: copy URL vào clipboard
  - **Rename project**: prompt sửa tên
  - **Pin/Unpin project**: ghim lên đầu sidebar (lưu DB)
  - **Delete project**: soft delete (giữ recordings cho audit)

### D. Xem Clean view (LLM format)

1. Click vào 1 phiên họp
2. Toggle tab **"Clean"** (cạnh "Raw")
3. Lần đầu: LLM chạy (~5-15s), kết quả cache vào DB
4. Lần sau: instant (cache hit)
5. Nút **↻** (refresh) cạnh tab → force re-run LLM

### E. Chọn mic / loa

1. Click 🎤 icon cạnh nút "Ghi âm"
2. Modal hiện list mic + loa
3. Chọn → Lưu → preference lưu localStorage
4. Lần record sau dùng mic đã chọn

---

## 🏗 Kiến trúc

```mermaid
flowchart TB
    subgraph FE["🖥 Frontend (Browser)"]
        direction TB
        FE1[Sidebar: Projects list]
        FE2[Workspace: Title + Transcript + MoM]
        FE3[Chat panel: HITL]
    end

    subgraph BE["⚙️ FastAPI Server &nbsp;&nbsp;:8001 HTTP &nbsp;·&nbsp; :9091 WSS"]
        direction TB

        subgraph API["API endpoints"]
            API1[meetings.py — REST]
            API2[chat.py — Chat + HITL]
        end

        subgraph SVC["Services"]
            SVC1[note_generator.py — LLM + chunking]
            SVC2[transcript_cleaner.py — Clean + cache]
            SVC3[memory_service.py — Cross-meeting memory]
        end

        subgraph LG["LangGraph"]
            LG1[mom_graph.py — 4 nodes]
            LG2[chat_graph.py — Chat HITL]
            LG3[checkpointer.py — AsyncPostgresSaver]
        end

        subgraph DB["DB layer (SQLAlchemy 2 async)"]
            DB1[models.py — ORM]
            DB2[repositories.py — SQL]
            DB3[base.py — Engine]
        end

        subgraph WSP["Whisper streaming"]
            WSP1[VAD buffer 8s]
            WSP2[Silence detection]
            WSP3[Hallucination filter]
            WSP4[VNG MaaS adapter]
        end

        API --> SVC
        API --> LG
        SVC --> DB
        LG --> DB
        WSP -.->|frames| API
    end

    subgraph EXT["☁️ External"]
        direction TB
        PG[("🗄 Postgres<br/>12 tables<br/>+ LangGraph checkpoints")]
        MAAS{{"🤖 VNG Cloud MaaS<br/>• Whisper API<br/>• Qwen3-5-27b LLM"}}
    end

    FE -->|HTTP + WSS| BE
    BE --> PG
    BE --> MAAS

    style FE fill:#d0ebff,stroke:#1971c2
    style API fill:#fff3bf,stroke:#e67700
    style SVC fill:#d3f9d8,stroke:#2f9e44
    style LG fill:#e5dbff,stroke:#862e9c
    style DB fill:#ffe8cc,stroke:#fd7e14
    style WSP fill:#ffdeeb,stroke:#d6336c
    style PG fill:#e7f5ff,stroke:#1971c2
    style MAAS fill:#fff9db,stroke:#f59f00
```

### DB Schema (12 tables)

3-cấp chính:
1. **`meetings`** (= Project) — container với title, attendees, mom_json, is_pinned
2. **`recordings`** (= Phiên họp) — session_label, started_at, clean_segments cache
3. **`transcript_segments`** — seq, original_text, edited_text per câu

Side tables:
- `users` — anchor entity (M365 OAuth defer)
- `meeting_members` — sharing role (owner/editor/viewer)
- `memory_events` — cross-meeting events (action_item/decision/...)
- `chat_sessions`, `chat_messages`, `pending_actions`, `audit_log` — Chat HITL
- `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` — LangGraph state

---

## 📂 Cấu trúc thư mục

```
mee-meeting-agent/
├── meeting/                    # Backend Python package
│   ├── api/
│   │   ├── meetings.py         # REST endpoints
│   │   └── chat.py             # Chat HITL endpoints
│   ├── db/
│   │   ├── base.py             # SQLAlchemy engine + session
│   │   ├── models.py           # ORM models
│   │   └── repositories.py     # SQL queries
│   ├── graphs/
│   │   ├── mom_graph.py        # MoM LangGraph
│   │   ├── chat_graph.py       # Chat LangGraph
│   │   └── checkpointer.py     # AsyncPostgresSaver
│   ├── services/
│   │   ├── memory_service.py   # Cross-meeting memory
│   │   ├── transcript_cleaner.py # Clean view LLM
│   │   └── tools.py            # Chat tools registry
│   ├── app.py                  # FastAPI factory
│   ├── note_generator.py       # MoM LLM (chunking + prompts)
│   ├── report_generator.py     # MoM JSON → Markdown
│   └── memory_client.py        # Legacy AgentBase memory (defer)
│
├── meeting_frontend/           # Frontend SPA
│   ├── index.html              # All UI markup + JS modules
│   ├── app.js                  # Recording + MoM generation logic
│   ├── style.css               # Dark theme + GreenNode green
│   └── audioprocessor.js       # AudioWorklet for mic capture
│
├── whisper_live/               # Whisper streaming backend
│   └── backend/
│       ├── base.py             # Base ServeClient
│       └── maas_backend.py     # VNG MaaS adapter
│
├── alembic/                    # DB migrations
│   ├── env.py
│   └── versions/
│       ├── 0001_initial_schema.py
│       ├── 0002_chat_tables.py
│       ├── 0003_memory_events.py
│       ├── 0004_meeting_pin.py
│       └── 0005_clean_cache.py
│
├── output/                     # Generated MoM .md files
├── docs/                       # Internal docs
├── run_meeting.py              # Main entry point
├── main.py                     # Alternative entry (uvicorn)
├── docker-compose.yml          # Local Postgres + Adminer (profile=local)
├── alembic.ini
├── requirements.txt
├── .env.example
└── README.md                   # This file
```

---

## 🤝 License

Internal project — VNG Cloud / GreenNode AI team.

---

**Built with**: FastAPI · SQLAlchemy 2 async · LangGraph · langchain-text-splitters · openai SDK · VNGCloud MaaS (Whisper + Qwen3-5-27b)
