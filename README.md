# Mee — AI Meeting Agent for Vietnamese Teams

> Turn Vietnamese meetings into clean transcripts, AI-drafted minutes, and tracked action items — **without losing a single commitment**.

---

## 📝 Submission summary (272 chars — copy-paste into the form)

```text
Mee — AI meeting agent for Vietnamese teams. PROBLEM: minutes take hours; action items get dropped. USERS: VN engineering, product, CS teams. SOLUTION: STT + diarize → editable transcript → AI-drafted MoM → Redmine tasks via HITL agent. VALUE: 2-4 hours saved per meeting.
```

## The problem

- Vietnamese meetings produce hours of untranscribed audio. Manual minutes take 1-3 hours per call and arrive a day later (if at all).
- **Action items mentioned mid-call get dropped** — by the time someone asks "wait, who's doing the deploy?", nobody remembers.
- **Cross-meeting context is invisible.** Decisions from last sprint's retro are lost when planning this sprint's stories.
- Existing tools (Otter, Fireflies, Notta) are English-first and stumble on Vietnamese, especially the Vietnamese-English code-switching that's the norm in tech meetings ("deploy", "API", "sprint", "code review" mixed with VN).

## Who it's for

- **Vietnamese engineering teams** running internal syncs, sprint reviews, technical design discussions, retros.
- **Product & customer-success teams** capturing interview / customer-call notes that feed roadmap decisions.
- **Project managers** who need their meetings to automatically convert into Redmine tasks without manual transcription.
- Any VN team that needs **privacy-preserving on-prem** STT + LLM (the entire stack is self-hostable on your own GPU / Postgres).

## How Mee solves it

Two parallel flows in one app:

**Flow 1 — Transcript (passive ingestion)**

1. Record / upload / paste audio in any of mp3, wav, m4a, or live mic
2. STT + speaker diarization (self-hosted PhoWhisper / faster-whisper + pyannote, OR VNG MaaS Whisper)
3. LLM cleans the raw transcript and merges per-speaker turns
4. User edits segments inline in a TipTap WYSIWYG editor; speaker names persist across recordings via a **voiceprint database**
5. **LangGraph 4-node MoM graph** drafts the minutes: summary + decisions + action items + commitments + blockers
6. **Project summary** aggregates every recording on a timeline narrative

**Flow 2 — Chat agent (active + Human-in-the-Loop)**

1. Ask anything about the meeting — "what did Nhi commit to?", "list overdue Redmine issues", "summarize this week's decisions"
2. Agent classifies intent → proposes a tool call (`create_task`, `send_email`, `search_transcript`, `retrieve` from memory, etc.)
3. **Side-effect tools require explicit approval.** The user sees the proposed action, can edit args, then approves or rejects — enforced by LangGraph `interrupt()`
4. Approved tools execute against the local DB, **Microsoft Graph** (Mail.Send, Calendar), or **pm-agent over A2A v0.3** (auto-create Redmine tasks)
5. Outcomes write back into memory + transcript so the next chat call sees them

**Cross-cutting**

- **Hybrid memory** — keyword tsvector + bge-m3 (1024-dim) vector + RRF fusion + optional rerank — so past decisions resurface
- **Speaker recognition across meetings** via voice enrollment + voiceprint DB (cosine match)
- **Multi-model picker** — switch STT (Whisper / PhoWhisper / faster-whisper) and LLM (Gemma 4 / Qwen 3.5 / GPT-OSS) per recording

## Value brought

- **2-4 hours saved per meeting** — manual transcription + minutes drafting eliminated
- **Zero dropped action items** — agent surfaces commitments and prompts before forgetting
- **Vietnamese-first quality** — Vietnamese-English code-switching tolerated; project vocabulary learned from user edits and pooled across the team
- **Privacy-preserving** — the entire stack is self-hostable (your GPU for STT, your vLLM for LLM, your Postgres for data); no audio leaves your infrastructure if you don't want it to
- **HITL by default** — every side-effect (email send, task create, calendar invite) goes through explicit user approval; the agent cannot act unilaterally

## Demo + docs

- **Implementation runbook + DB schema + setup**: see the Vietnamese tech sections below ↓
- **For new dev sessions on this repo**: read [`HANDOFF.md`](./HANDOFF.md) + [`CLAUDE.md`](./CLAUDE.md) + [`PLAN.md`](./PLAN.md)
- **Canonical product docs (Obsidian)**: `~/greennode/GreenNode/Meeting Agent/` (vault)

---

*Implementation guide continues below in Vietnamese.*

---

# Mee — Meeting Note Agent (tài liệu kỹ thuật)

> AI meeting agent cho tiếng Việt: **paste / upload audio / live record** → transcript → **WYSIWYG Clean editor** → **Biên bản phiên họp (MoM)** + **Tổng kết project**. Powered by LangGraph + Qwen3 LLM + PhoWhisper STT.

**🌐 Mee-docs** — [docs](https://endpoint-e2c26683-c6aa-4f05-8502-57eec4d78c35.agentbase-runtime.aiplatform.vngcloud.vn)

---

## ✨ Tính năng chính

### Luồng transcript (passive)

| Feature | Mô tả |
|---|---|
| **Project / Phiên họp 2 cấp** | Project (folder) chứa N phiên họp. Mỗi phiên = 1 transcript + 1 biên bản riêng |
| **3 input modes** | Paste text · Upload audio (mp3/wav/m4a, auto-chunk >24MB) · Live record (mic + WebSocket :9091 → Whisper streaming) |
| **STT linh hoạt** | VNG MaaS Whisper-large-v3 (mặc định), hoặc self-host PhoWhisper / **faster-whisper** + pyannote trên GPU (L40 / 2080 — xem `HANDOFF.md §7`) |
| **Speaker diarization** | pyannote 3.1 local hoặc remote (Kaggle GPU tunnel). Auto-cluster speakers + assign cluster_id |
| **Voice enrollment + Voiceprint DB** | User enroll giọng 1 lần sau login. Voiceprint (cosine match) tự nhận diện speaker cross-meeting |
| **Per-recording attendance** | Tick teammate có mặt trong phiên → backend forward count làm min/max-speakers hint cho pyannote (chính xác hơn cho clip ngắn) |
| **TipTap WYSIWYG Clean editor** | Edit transcript inline với bold/italic/lists/headings + tag chips (commitment/decision/blocker) + auto-save 1.5s |
| **Karaoke word-sync** | Word-level timestamps từ faster-whisper → highlight từng từ theo audio playback (Notta-style) |
| **MoM per-recording** | Biên bản sinh cho **1 phiên cụ thể**, lưu `recordings.mom_json`. MoM ưu tiên `edited_text` (đã sửa) thay vì raw |
| **Project summary** | Tổng kết toàn project = timeline decisions theo `started_at` + narrative LLM aggregate từ N MoM |
| **MoM language picker** | Chọn VN/EN cho biên bản per-recording (default = ngôn ngữ UI hiện tại) |
| **Multi-model picker** | Chọn STT (Whisper / PhoWhisper / faster-whisper) + LLM (Gemma 4 / Qwen 3.5 / GPT-OSS) per recording hoặc per meeting |

### Luồng chat agent (active + HITL)

| Feature | Mô tả |
|---|---|
| **Chat HITL** | LangGraph 5-node graph: classify intent → propose tool call → **interrupt() chờ user approve** → execute → write back |
| **Tool registry** | `list_recordings` · `recording_mom` · `search_transcript` · `retrieve` (memory) · `create_task` · `send_email` · `switch_meeting` |
| **pm-agent A2A** | `create_task` gọi sang external pm-agent (Redmine) qua A2A v0.3 JSON-RPC (`PM_AGENT_URL`) |
| **MS Graph tools** | `send_email` qua Mail.Send · `find_meeting_availability` qua Calendars.ReadWrite |
| **Hybrid memory** | Cross-meeting memory: keyword tsvector + bge-m3 vector (1024-dim pgvector) + RRF fusion + optional LLM rerank |

### Collaboration + UI

| Feature | Mô tả |
|---|---|
| **O365 Auth** | Real MSAL OAuth2 PKCE (Azure Entra) hoặc mock provider (dev). Session cookie HMAC-signed + AES-256-GCM refresh token at rest |
| **Sharing model** | Owner / Editor / Viewer per project. Invite by email với autocomplete (`/users/search`) |
| **Per-recording comments** | Anchor comment vào audio timestamp. Click ▸ M:SS → tua audio đến đó. Sprint 05 |
| **Conversation insights** | Per-speaker stats: talk ratio, monologues count, longest monologue, pace (wpm), silence vs speaking bar. Sprint 05 |
| **Floating rail** | 4-button right edge: AI chat · MoM document · Comment · Insights. Mutually exclusive. Sprint 05 |
| **Sidebar icon-rail collapse** | 56px wide khi đóng — hiện chữ cái project + avatar. Click Mee logo: đóng → mở; mở → về landing. Sprint 05 |
| **Audio device picker** | Settings cog → chọn mic + loa. Default = OS. Speaker route qua `audio.setSinkId()`. Sprint 05 |
| **Sidebar context menu** | Hover project → ⋮ → Share / Rename / Pin / Delete. Hover phiên → × delete |
| **i18n VI/EN** | Toàn bộ UI có 2 ngôn ngữ, switch trong settings. ~400 keys |
| **Theme dark/light** | Persist localStorage. Default = dark (GreenNode aesthetic) — radial green glow ở top |

### Infrastructure

| Feature | Mô tả |
|---|---|
| **Background tasks** | Celery + RabbitMQ — `gen_mom_task`, `clean_recording_task`, `diarize_recording_task`. Default `pool=solo`; production → `prefork` + concurrency |
| **R2 object storage** | Audio files + voice samples saved tới Cloudflare R2 (cấu hình `R2_*` env vars). Fallback local disk khi unconfigured |
| **LangGraph checkpointer** | `AsyncPostgresSaver` resume từ failed node (thread_id = recording_id). MomGraph 4 nodes, ChatGraph 5 nodes |
| **Vocab learning** | User-edit transcript → cross-project vocab pool → bias Whisper initial_prompt cho recording sau |

---

## 🔄 Workflow

Mee có **2 luồng chạy song song**:

```mermaid
flowchart TB
    subgraph Auth["🔐 Auth + Enrollment"]
        L["👤 Landing page"] -->|Click Login| MOCK[Mock O365 / MSAL]
        MOCK -->|OAuth callback| S[Session cookie]
        S --> VE{Voice enrolled?}
        VE -->|No| ENR["🎤 Voice enrollment<br/>/onboard/voice"]
        ENR -->|POST /voiceprints/enroll| VP[voiceprints table]
        VP --> VE
        VE -->|Yes| APP[App /app]
    end

    subgraph Flow1["📝 Luồng Transcript (passive)"]
        APP --> F1A[Create Project<br/>+ Recording]
        F1A --> F1B{Input}
        F1B --> P1[📝 Paste text]
        F1B --> U1[📁 Upload audio]
        F1B --> R1[🎤 Live record]
        
        U1 --> STT[STT]
        R1 --> WSS[WSS :9091]
        WSS --> STT
        P1 --> RAW[Raw transcript]
        STT --> RAW
        
        RAW --> DIA[🔊 Diarize<br/>pyannote]
        DIA --> CLS[Cluster → speaker<br/>+ cluster_id]
        
        CLS --> CLN{Optional}
        CLN -->|Click Clean| CL[🪶 TipTap Editor<br/>LLM clean + edit]
        CL -->|PATCH /clean-edited| CLSeg[clean_segments<br/>edited_text]
        
        CLS --> MOM[📋 Gen MoM]
        CLSeg -->|preferred| MOM
        MOM --> MGraph[🧠 MomGraph 4 nodes]
        MGraph --> MOMJson[recordings.mom_json]
        MOMJson --> UI1[MoM Pane]
        
        MOM --> MEM[memory_events<br/>+ bge-m3 vector]
        
        UI1 --> PS[📊 Project Summary]
        PS --> PSSum[meetings<br/>project_summary_json]
    end

    subgraph Flow2["💬 Luồng Chat Agent (active + HITL)"]
        APP --> C1[💬 Chat input]
        C1 --> CG[🧠 ChatGraph 5 nodes]
        
        CG --> C2[Classify intent]
        C2 --> C3[Propose tool call]
        C3 --> HITL{"⚠️ HITL<br/>interrupt()"}
        
        HITL -->|Approve| EX[Execute tool]
        HITL -->|Reject| C1
        
        EX --> T1[pm-agent A2A<br/>Redmine]
        EX --> T2[MS Graph<br/>Mail.Send/Calendar]
        EX --> T3[Internal<br/>list/search]
        
        T1 --> WB[Write back]
        T2 --> WB
        T3 --> WB
        WB --> MEM2[memory_events]
        WB --> C4[Response to UI]
    end

    style Auth fill:#e0f2fe,stroke:#0284c7
    style Flow1 fill:#f0fdf4,stroke:#16a34a
    style Flow2 fill:#faf5ff,stroke:#9333ea
    style STT fill:#fff7ed,stroke:#f97316
    style CLN fill:#fef9c3,stroke:#eab308
    style MGraph fill:#f3e8ff,stroke:#7c3aed
    style CG fill:#f3e8ff,stroke:#7c3aed
    style HITL fill:#fee2e2,stroke:#dc2626
```

### Chi tiết từng node

| Node | Vai trò |
|---|---|
| **Auth** | O365 OAuth2 PKCE (Azure Entra) hoặc mock provider. Session cookie HMAC-signed |
| **Voice enrollment** | User đọc slogan → enroll 1 lần → voiceprints table → cosine match cross-meeting |
| **STT** | VNG MaaS Whisper-large-v3 (default) hoặc self-host faster-whisper |
| **Diarize** | pyannote 3.1 → cluster → speaker labels. Attendance hint (len(attendees) ±1) cho clip ngắn |
| **Clean** | LLM transcript cleaner → TipTap editor → user edit → `clean_segments.edited_text` |
| **MomGraph** | LangGraph 4 nodes: load_transcript → read_memory → generate_mom → save_results |
| **ChatGraph** | LangGraph 5 nodes: classify → propose → **interrupt()** → execute → write_back |
| **HITL** | User approve/reject trước khi tool execute (pm-agent, MS Graph, internal) |
| **pm-agent** | External A2A v0.3 → Redmine task creation |
| **MS Graph** | Mail.Send, Calendars.ReadWrite |

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
- **HTTP API** ở `http://localhost:8002`
- **WebSocket transcription** ở `ws://localhost:9091`

### 6. Run React frontend — UI (cài package Node)

Yêu cầu **Node.js ≥ 18** (kiểm tra: `node -v`). Lần đầu phải `npm install` để tải toàn bộ
package UI (React 18, Vite, TipTap, …) vào `node_modules/` — chỉ cần chạy lại khi `package.json` đổi.

```bash
cd meeting_frontend_react
npm install        # cài dependencies UI (lần đầu, hoặc khi package.json thay đổi)
npm run dev        # chạy Vite dev server
```

Vite dev server ở `http://localhost:8001` (trùng host callback OAuth đã đăng ký trên Azure). Proxy `/api` + `/auth` → backend `:8002`, `/ws` → `:9091`.

Build cho production: `npm run build` → `dist/`.

> **Lỗi npm thường gặp:** nếu `npm install` báo xung đột peer-deps → thử `npm install --legacy-peer-deps`.
> Đừng commit `node_modules/` (đã gitignore). Backend phải chạy trước (`:8002`) thì proxy `/api` mới hoạt động.

Legacy vanilla frontend vẫn ở `meeting_frontend/`, served bởi FastAPI tại `http://localhost:8002/`.

### 7. (Optional) Self-host STT + Diarize

Nếu muốn STT chất lượng cao tiếng Việt + speaker diarization:

```bash
# Tùy chọn A: Deploy lên L40 GPU (48GB VRAM)
cd tools/phowhisper-server
# Đọc README.md để deploy

# Tùy chọn B: Deploy lên RTX 2080 (11GB VRAM) - tight nhưng doable
# Xem chi tiết trong HANDOFF.md §7
```

Server expose `/v1/audio/transcriptions` OpenAI-compatible. Update `WHISPER_BASE_URL` trong `.env`.

**Hybrid routing** (recommended):
- Audio < 5 phút → AgentBase CPU runtime
- Audio > 5 phút → Kaggle GPU tunnel (free)
- Both unreachable → local CPU fallback

Xem `HANDOFF.md §7` để setup `nhihb-gpu-2080` self-host với SSH tunnel.

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

### E. Voice enrollment (first-time)

1. Login lần đầu → redirect `/onboard/voice`
2. Đọc slogan (VI + EN, ~15-30s): `AI Cloud hiệu năng cao dành riêng cho doanh nghiệp số`
3. Click record → cho phép mic → đọc xong → stop
4. Nghe playback → submit → voiceprints enrolled
5. Từ giờ speaker sẽ tự nhận diện cross-meeting qua cosine match

### F. Comments + Insights

1. **Comments**: Trong workspace phiên → click 💬 floating rail → gõ comment → nhấn Ctrl+Enter
2. Comment anchor vào `audio.currentTime` → click ⏱ M:SS chip → tua audio đến đó
3. **Insights**: Click 📊 floating rail → xem per-speaker stats (talk ratio, monologues, longest monologue, pace wpm, silence bar)

### G. Members + Invite

1. Click 👥 Members panel trong sidebar → xem danh sách
2. **Invite**: Gõ email → autocomplete `/users/search` → chọn → role (Editor/Viewer)
3. **Per-recording attendance**: Tick checkbox teammate có mặt trong phiên → backend dùng count làm min/max-speakers cho pyannote

### H. Audio devices

1. Click ⚙️ Settings (gear icon) → **Audio devices**
2. Chọn mic + speaker từ dropdown (lọc duplicate browser aliases)
3. Mic dùng cho live recording, speaker dùng cho playback trong NottaCleanView

### I. Quản lý

- **Hover project** → ⋮ menu: Share / Pin / Rename / Delete (red)
- **Hover phiên** → × delete
- **Settings (gear icon)** → Theme (light/dark) + Language (VI/EN) + Audio devices
- **Floating rail (phải)** → AI chat / MoM doc / Comments / Insights (mutually exclusive)
- **Sidebar collapse** → click Mee logo khi collapsed → mở; khi open → về landing

---

## 🏗 Kiến trúc

```mermaid
flowchart TB
    subgraph FE["🖥 Frontend"]
        FE1[React 18 + TS + Vite<br/>+ TipTap + Zustand]
        FE2[Legacy vanilla JS<br/>meeting_frontend/]
    end

    subgraph BE["⚙️ FastAPI Backend"]
        AUTH[Auth<br/>mock · microsoft]
        API[API endpoints<br/>meetings.py · chat.py]
        SVC[Services<br/>memory · cleaner · summarizer<br/>embedding · reranker · vocab]
        CEL[Celery tasks<br/>gen_mom · clean · diarize]
        LG[LangGraph<br/>MomGraph · ChatGraph<br/>AsyncPostgresSaver]
        WSP[WebSocket<br/>:9091 live STT]
    end

    subgraph MQ["🐇 Message Queue"]
        RABBIT[RabbitMQ]
    end

    subgraph EXT["☁️ External services"]
        STT[VNG MaaS Whisper<br/>OR self-host faster-whisper]
        LLM[VNG MaaS<br/>Qwen3 / Gemma / GPT-OSS<br/>OR self-host vLLM]
        EMB[VNG MaaS<br/>bge-m3 (1024-dim)]
        RER[VNG MaaS<br/>bge-reranker]
        PM[pm-agent<br/>A2A v0.3 Redmine]
        MS[Microsoft Graph<br/>Mail.Send / Calendar]
        R2[Cloudflare R2<br/>audio + voice samples]
    end

    subgraph GPU["🚀 Self-host GPU"]
        KAGGLE[Kaggle T4<br/>pyannote remote]
    end

    PG[("🗄 Postgres + pgvector<br/>15 tables")]

    FE -->|HTTP / WSS| BE
    AUTH -.->|OAuth callback| FE
    API --> SVC
    API --> LG
    SVC --> CEL
    CEL --> RABBIT
    RABBIT --> CEL
    LG --> SVC
    SVC --> PG
    LG --> PG
    WSP --> STT
    SVC --> EMB
    SVC --> RER
    SVC --> LLM
    LG --> LLM
    SVC --> R2
    CEL --> KAGGLE
    LG --> PM
    LG --> MS

    style FE fill:#d0ebff,stroke:#1971c2
    style BE fill:#fff3bf,stroke:#e67700
    style MQ fill:#ffedd5,stroke:#ea580c
    style EXT fill:#ffe8cc,stroke:#fd7e14
    style GPU fill:#fce7f3,stroke:#db2777
    style PG fill:#e7f5ff,stroke:#1971c2
```

### DB Schema (15 tables)

| Cấp | Table | Description |
|---|---|---|
| 1 | `users` | Auth — email, display_name, avatar_url, voice_enrolled, ms_tenant_id, ms_oid |
| 1 | `voiceprints` | User voice embeddings (wespeaker 256-d) |
| 2 | `meetings` | Project — title, attendees, is_pinned, project_summary_json, vocab_hints, stt_model, llm_model |
| 2 | `meeting_members` | Owner / Editor / Viewer per user × meeting |
| 3 | `recordings` | Phiên — session_label, started_at, mom_json, clean_segments, attendees, vocab_hints |
| 3 | `transcript_segments` | Raw STT — seq, original_text, edited_text, speaker, cluster_id, words[] (start_ms/end_ms) |
| 3 | `recording_comments` | Anchor timestamp + text (Sprint 05) |
| 3 | `speaker_samples` | 3s WAV per cluster for voice preview |
| Side | `memory_events` | Cross-meeting facts + bge-m3 vector(1024) + tsvector |
| Chat | `chat_sessions` · `chat_messages` · `pending_actions` | Chat HITL |
| Vocab | `vocab_pool` | Learned terms from user edits |
| LangGraph | `checkpoints` · `checkpoint_writes` · `checkpoint_blobs` | Resume state |

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
│   ├── vite.config.ts                # Vite :8001, proxy /api+/auth → :8002, /ws → :9091
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
