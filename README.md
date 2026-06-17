<p align="center">
  <img src="frontend/public/favicon.png" alt="Mee logo" width="150" />
</p>

<h1 align="center">Mee — Meeting Note Agent</h1>

<p align="center">
  Vietnamese-first meeting intelligence for turning conversations into structured memory, actionable knowledge, and project continuity.
</p>

<p align="center">
  <a href="https://endpoint-e2c26683-c6aa-4f05-8502-57eec4d78c35.agentbase-runtime.aiplatform.vngcloud.vn">Read the docs</a>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-1C1C1C?style=for-the-badge" />
  <img alt="React" src="https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=111111" />
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-5-3178C6?style=for-the-badge&logo=typescript&logoColor=white" />
  <img alt="Vite" src="https://img.shields.io/badge/Vite-5-646CFF?style=for-the-badge&logo=vite&logoColor=white" />
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL%20%2B%20pgvector-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img alt="Docker" src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
</p>

---

## ✨ Key Features

| Feature | Description |
|---|---|
| **Two-level project/session model** | A project contains multiple meeting sessions. Each session has its own transcript and MoM. |
| **Three input modes** | Paste text · Upload audio (mp3/wav/m4a, auto-chunk over 24MB) · Live record (microphone → WebSocket → Whisper streaming). |
| **Per-recording MoM** | Generates minutes for one specific session and stores them in `recordings.mom_json`. Edited Clean transcripts are preferred as input. |
| **Project summary** | Aggregates the whole project into a decision timeline and LLM narrative from multiple MoMs. |
| **TipTap WYSIWYG Clean editor** | Edit transcripts inline with bold, italic, lists, headings, tag chips, and 1.5s autosave. |
| **Self-hosted PhoWhisper + pyannote** | Vietnamese STT with 8.85% WER plus speaker diarization on an L40 GPU, or VNG MaaS Whisper. |
| **Hybrid memory** | Cross-meeting retrieval with keyword tsvector, semantic bge-m3 vectors, RRF fusion, and optional LLM rerank. |
| **Conversation memory** | `remember_fact` and `forget_fact` let the agent persist user-provided facts in AgentBase and recall them in later turns. |
| **Chat HITL + Redmine** | Human-in-the-loop chat agent with direct Redmine operations through MCP. Write tools require approval. |
| **i18n VI/EN + theme** | Bilingual UI, dark/light themes, and localStorage persistence. |

---

## 🚀 Setup and Run

### Requirements

- Python ≥ 3.11 · Node.js ≥ 18 · Postgres ≥ 14 **with the pgvector extension** (remote or local Docker).
- VNG Cloud MaaS API key **or** self-hosted Qwen3 + bge-m3 + Whisper.

### 1. Backend (Python)

```bash
git clone <repo-url> && cd mee-meeting-agent

python -m venv venv                      # the repo standard is `venv`, not `.venv`
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
venv/bin/pip install "psycopg[binary]"   # required for LangGraph checkpointer; missing it causes "libpq not found"
```

> Do not commit `venv/` (it is gitignored). If `psycopg[binary]` has no wheel for your machine:
> `sudo apt-get install -y libpq5` on Debian/Ubuntu, or `brew install libpq` on macOS.

### 2. Configure `.env`

```bash
cp .env.example .env      # then fill in real values
```

Minimum variables required to run:

```env
# LLM (cleaner + MoM + chat) — self-hosted Qwen3 (vLLM) or VNG MaaS
LLM_BASE_URL=http://<llm-host>:8000/v1
LLM_API_KEY=EMPTY
LLM_MODEL=Qwen/Qwen3-8B

# Whisper (STT) — VNG MaaS or self-hosted PhoWhisper (see tools/phowhisper-server/)
WHISPER_BASE_URL=https://<maas-host>/.../whisper-large-v3
WHISPER_API_KEY=vn-...
WHISPER_MODEL=openai/whisper-large-v3

# Embedding — bge-m3 1024-d. Note: the code reads EMBED_* names, not EMBEDDING_*.
EMBED_BASE_URL=https://<maas-host>/.../bge-m3/v1
EMBED_API_KEY=vn-...
EMBED_MODEL=BAAI/bge-m3
EMBED_DIM=1024

# Database — pgvector must be enabled. The code adds driver prefixes automatically (+asyncpg / +psycopg2).
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

> dotenv is loaded with `interpolate=False`, so passwords containing `$` are preserved.
> Optional variables for O365 login, Redmine MCP, AgentBase memory, and tracing are documented in `.env.example`.

### 3. DB migrations

```bash
# Local Postgres+pgvector (skip this when using a remote DATABASE_URL):
docker compose --profile local up -d        # postgres:5435, adminer:8080

venv/bin/alembic upgrade head               # creates schema + pgvector + IVFFlat index
```

### 4. Run backend + frontend

```bash
# Terminal 1 — backend (HTTP :8002 + WebSocket :9091)
venv/bin/python run_meeting.py

# Terminal 2 — React frontend (Vite :8001, proxy /api+/auth→:8002, /ws→:9091)
cd frontend
npm install                                  # first run, or when package.json changes
npm run dev
```

Open **http://localhost:8001**. Vite runs on `:8001` because that host is registered as the Azure OAuth callback.
Production build: `npm run build` → `dist/`.

> The backend must be running on `:8002` before the `/api` proxy works. If `npm install` hits peer dependency errors, try `--legacy-peer-deps`.

---

## 🧭 Workflow

```text
[Paste text / Upload audio / Live record]
        │   (audio → POST /api/transcribe or WSS :9091 → Whisper)
        ▼
   Raw transcript ──/import-transcript──▶ transcript_segments
        │
        ├─ (optional) "Clean" tab → TipTap editor (LLM clean + user edit) → clean_segments.edited_text
        ▼
   "Current session MoM"  POST /recordings/{id}/generate-mom
        ▼
   MomGraph: load_transcript(edited > raw) → read_memory(hybrid) → generate_mom(Qwen3 map-reduce) → save_results
        ▼
   recordings.mom_json  +  memory_events(+bge-m3 vector)  +  output/MoM_*.md  →  MoMPane

   "Project summary"  /generate-project-summary
        ▼
   project_summarizer aggregates mom_json by started_at → meetings.project_summary_json
```

---

## 🖱 Usage

1. Sidebar → **"+ Project"** → creates "Session 1" automatically → enter the workspace.
2. Choose an input mode: **Paste** · **Upload** · **Record**.
3. Optional: open **"Clean"** → run LLM cleaning → edit inline + highlight and tag text → autosave in 1.5s or press Ctrl+S.
4. Click **"Current session MoM"** → the MoM appears in MoMPane.
5. Add another session with **"+ New meeting session"** and repeat.
6. Click **"Project summary"** from the project level, not a session, to generate the decision timeline and narrative.
7. Manage projects and sessions: hover project → ⋮ (Share/Pin/Rename/Delete) · hover session → × · gear icon → theme + language.

---

## 🏗 Architecture

```mermaid
flowchart LR
    subgraph Client["Client Experience"]
        direction TB
        UI["React Workspace<br/>Vite · TipTap · i18n"]
        Input["Meeting Inputs<br/>Paste · Upload · Live Record"]
        UI --> Input
    end

    subgraph Core["Mee Backend"]
        direction TB
        API["FastAPI Gateway<br/>REST · WebSocket"]
        Routers["Domain APIs<br/>meetings · chat · auth"]
        Graphs["LangGraph Orchestration<br/>MomGraph · ChatGraph"]
        Services["Service Layer<br/>cleaner · memory · reranker"]
        API --> Routers
        Routers --> Graphs
        Routers --> Services
        Graphs --> Services
    end

    subgraph Data["State + Memory"]
        direction TB
        DB[("Postgres + pgvector<br/>transcripts · MoM · memory")]
        CP[("LangGraph Checkpoints<br/>resume state")]
    end

    subgraph AI["AI Runtime"]
        direction TB
        STT["Speech-to-text<br/>PhoWhisper + pyannote / VNG MaaS"]
        LLM["Reasoning + generation<br/>Qwen3 vLLM / VNG MaaS"]
        EMB["Embeddings<br/>bge-m3"]
    end

    subgraph Integrations["External Integrations"]
        direction TB
        Redmine["Redmine MCP<br/>HITL write approval"]
        AgentBase["AgentBase Memory<br/>remember_fact / forget_fact"]
    end

    Input -->|HTTP + WebSocket| API
    Routers <-->|CRUD + queries| DB
    Graphs -->|checkpoints| CP
    Services <-->|hybrid retrieval| DB
    API -->|audio| STT
    Services -->|clean · summarize · rerank| LLM
    Graphs -->|agent + MoM generation| LLM
    Services -->|semantic vectors| EMB
    Services <-->|long-term facts| AgentBase
    Graphs <-->|approved actions| Redmine

    classDef client fill:#e0f2fe,stroke:#0284c7,color:#0f172a
    classDef core fill:#fef3c7,stroke:#d97706,color:#0f172a
    classDef data fill:#dcfce7,stroke:#16a34a,color:#0f172a
    classDef ai fill:#fae8ff,stroke:#9333ea,color:#0f172a
    classDef integration fill:#ffe4e6,stroke:#e11d48,color:#0f172a

    class UI,Input client
    class API,Routers,Graphs,Services core
    class DB,CP data
    class STT,LLM,EMB ai
    class Redmine,AgentBase integration
```

### DB schema

| Level | Table | Description |
|---|---|---|
| 1 | `meetings` | Project: title, attendees, is_pinned, project_summary_json. |
| 2 | `recordings` | Meeting session: session_label, started_at, mom_json, clean_segments. |
| 3 | `transcript_segments` | Sentence-level raw segments: seq, original_text, edited_text. |
| Side | `users` · `meeting_members` · `memory_events` (+ embedding vector(1024)) | Auth, sharing, and memory. |
| Chat | `chat_sessions` · `chat_messages` · `pending_actions` · `audit_log` | Human-in-the-loop chat. |
| LangGraph | `checkpoints` · `checkpoint_writes` · `checkpoint_blobs` | Resumable state (`thread_id = recording_id`). |

> Migrations `0001` through `0023` live in `alembic/versions/`. Schema milestones include pgvector/IVFFlat (`0006`), two-level MoM (`0007`), user-scoped chat (`0022`), and recording comments (`0023`).

---

## 🔗 Chat Agent ↔ Redmine (MCP)

The chat agent operates on Redmine **directly through an MCP server** (`MCP_REDMINE_URL`, Bearer token = Redmine API key).
Tools are **discovered dynamically** with `list_tools()` at startup and cached on disk in `.mcp_redmine_tools_cache.json`.
Write tools (`create_redmine_issue`, `update_redmine_issue`, `bulk_update_issues`) are gated by HITL, so the user must approve them before execution.
pm-agent (A2A) only runs when the user types the `/pm-agent` prefix, which keeps routing deterministic.

**MoM → Redmine sync (`create_task`):** the agent builds a task list from the MoM, asks for **one approval** for the whole batch, then applies it through MCP in the `agent_execute` node. Each item maps to `create_redmine_issue`, or `update_redmine_issue` when an `issue_id` exists. `due_date` is normalized to `YYYY-MM-DD`; invalid values are dropped.

> Env: `MCP_REDMINE_URL`, `REDMINE_API_KEY` (or a per-user key through AgentBase Identity). Dependency: `mcp>=1.25.0`.
> Probe: `venv/bin/python scripts/probe_redmine_mcp.py`.

---

## 🧠 Conversation Memory — remember_fact / forget_fact

The agent stores facts mentioned in chat as **long-term memory** in **AgentBase memory records**, not `memory_events`.
This memory survives "Clear conversation". It has two scopes:

| scope | namespace | used for |
|---|---|---|
| `user` | `user_prefs/<ms_oid>` | User-specific facts such as preferred name and preferences. |
| `project` | `project_facts/<meeting_id>` | Shared project facts partitioned by `meeting_id`. |

- The two tools run without approval and write in the background (fire-and-forget). Missing `MEMORY_ID` → no-op.
- AgentBase is **insert-only** for this service account (DELETE returns 403): `forget_fact` writes a tombstone (`active=0`); recall uses newest-wins by `key` and hides tombstones.
- `load_context` injects user facts and project facts for the current session into the **"Memory"** prompt block, capped at the 20 newest items.

> Inspect effective memory with: `venv/bin/python scripts/dump_agent_memory.py <meeting_id> <ms_oid>`.
> Spec: `docs/superpowers/specs/2026-06-16-chat-knowledge-capture-design.md`.

---

## 📂 Project Structure

```text
mee-meeting-agent/
├── src/                          # Backend Python package
│   ├── api/                      # meetings.py (REST) · chat.py (HITL)
│   ├── db/                       # base.py (engine) · models.py (ORM) · repositories.py
│   ├── graphs/                   # mom_graph.py · chat_graph.py · checkpointer.py
│   ├── services/                 # memory · transcript_cleaner · project_summarizer · embedding · reranker
│   ├── app.py                    # FastAPI factory
│   ├── note_generator.py         # MoM LLM (map-reduce + Qwen3 think-strip)
│   └── report_generator.py       # MoM JSON → Markdown
├── frontend/                     # React frontend: Vite + React 18 + TS + TipTap
│   └── src/                      # api/client.ts · store/AppContext.tsx · hooks/ · i18n.ts · components/
├── whisper_live/                 # Whisper streaming backend (maas_backend.py)
├── tools/phowhisper-server/      # Self-hosted PhoWhisper + pyannote (deploy L40)
├── alembic/versions/             # DB migrations 0001-0023
├── scripts/                      # backfill_embeddings · dump_agent_memory · probe_* …
├── run_meeting.py                # Main entry (HTTP :8002 + WebSocket :9091)
├── docker-compose.yml            # Local Postgres+pgvector (profile=local)
├── requirements.txt · .env.example · README.md
```

---

## 🤝 License

Internal project — VNG Cloud / GreenNode AI team.

**Built with**: FastAPI · SQLAlchemy 2 async · LangGraph · pgvector · openai SDK · React 18 · TypeScript · Vite · TipTap · Qwen3 · bge-m3 · PhoWhisper-large · pyannote 3.1
