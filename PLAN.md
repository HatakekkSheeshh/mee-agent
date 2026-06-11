# Mee Meeting Agent — Master Plan

> **Working document.** Updated as we go. Survives conversation compaction.
> Single source of truth for sprint planning, decisions, and context.
>
> **Last updated**: 2026-06-10
> **Owner**: nhihb@vng.com.vn

---

## How to use this file

- `- [ ]` = chưa làm
- `- [~]` = đang làm (in progress)
- `- [x]` = xong
- `- [-]` = won't do / deferred indefinitely (kèm lý do)

**Future Claude / new sessions**: Đọc file này trước khi đề xuất việc gì. Update file sau mỗi sprint completion. Khi user nói "task X done" → tick checkbox + thêm note vào **Done** section nếu significant.

**Update rules**:
- Khi task complete → move từ active section sang **Done** (giữ checkbox tick)
- Khi quyết định kiến trúc → append vào **Decisions log** với date
- Khi ý tưởng mới → add vào P0/P1/P2/P3 phù hợp với justification
- KHÔNG xoá item — chỉ tick + có thể strikethrough ~~text~~ nếu obsolete

---

## Quick navigation

| Section | Mục đích |
|---|---|
| [P0 — This sprint](#p0--this-sprint) | Tuần này / 1-2 tuần tới — must-do |
| [P1 — Next 2-4 weeks](#p1--next-2-4-weeks) | Sau hackathon nếu deploy production |
| [P2 — Backlog](#p2--backlog) | Nice-to-have, không block release |
| [P3 — Vision](#p3--vision) | Long-term, ý tưởng lớn |
| [Done](#done) | Lịch sử + context của việc đã hoàn thành |
| [Decisions log](#decisions-log) | Quyết định kiến trúc + lý do |
| [Reference](#reference) | Links / paths / external services |

---

## P0 — This sprint

### 🔐 Auth + Voice enrollment (NEW — confirmed 2026-06-10)

**Context**: IT chưa cấp quyền O365 → mock login first, swap when ready. Voice enrollment 1 lần sau login → ground truth cho speaker matching trong tất cả meeting của user đó.

**Slogan enrollment** (confirmed):
- VI: `AI Cloud hiệu năng cao dành riêng cho doanh nghiệp số`
- EN: `High performance AI Cloud for digital-native business`
- 15-30s total (đọc cả 2 câu)

- [x] Landing page (`/`) — GreenNode-style dark theme, single-page with smooth scroll, 6 capabilities + 6 use cases (production-ready, no tech jargon)
- [x] Mock login page (`/auth/mock-login`) — full mimic MS UI
- [x] Auth backend: `meeting/auth/` package
  - [x] `base.py` — `AuthProvider` Protocol + `UserInfo` dataclass
  - [x] `mock.py` — `MockProvider` (returns hardcoded user info)
  - [x] `microsoft.py` — `MicrosoftProvider` stub (MSAL ready when IT grants)
  - [x] `session.py` — HMAC-signed cookie httpOnly, `get_current_user` dep
  - [x] `routes.py` — `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me`
- [x] DB migration `0017_users_auth_voice.py`:
  - [x] `users.email` (unique constraint added)
  - [x] `users.display_name`, `users.avatar_url`
  - [x] `users.ms_tenant_id`, `users.ms_oid` (existing, relaxed to nullable)
  - [x] `users.voice_enrolled` bool
  - [x] `users.created_at`, `users.last_login_at` (existing — unchanged)
- [x] Voice enrollment page (`/onboard/voice`)
  - [x] UI: slogan VI + EN display in glass card with green accent
  - [x] Record button + live audio level visualizer (AnalyserNode-driven concentric pulse rings)
  - [x] Playback with custom audio player (native dark control replaced)
  - [x] Submit → POST `/api/voiceprints/enroll`
- [x] Backend `POST /api/voiceprints/enroll` (Phase 1 minimal — saves WAV + flips flag)
  - [x] Accept WAV/webm multipart
  - [ ] Call existing wespeaker embed (256-d) ← Phase 2, currently just saves to disk
  - [ ] Save to `voiceprints` table với label="enrollment" ← Phase 2
  - [x] Update `users.voice_enrolled = true`
- [x] FE auth guard (react-router-dom v7)
  - [x] On app load: GET `/auth/me`
  - [x] 401 → redirect `/`
  - [x] 200 + `voice_enrolled=false` → redirect `/onboard/voice`
  - [x] 200 + `voice_enrolled=true` → main app `/app`
- [x] `.env.example` thêm `AUTH_PROVIDER=mock` + `SESSION_SECRET` + MS_* vars (commented)
- [ ] **Swap to real O365** (khi IT grant) — chỉ đổi 1 env var

**Effort**: 2.5-3 ngày mock-ready. Real O365 swap +2-4h.
**Status (2026-06-11)**: Phase 1 backend hoàn thành. Verified end-to-end qua HTTP TestClient (login → mock-submit → callback → user created in DB → cookie issued). Phase 2 = voice enrollment page + endpoint. Phase 3 = landing page + FE auth guard.

### 🚢 Deploy pyannote → AgentBase

**Context**: Image đã build (~7 GB), đã push registry. Còn deploy + wire vào Mee.

- [x] Build Docker image `mee/pyannote:0.1` — 4 bug fixes (python3.11, hf_hub<1.0, use_auth_token, port 8080+/health)
- [x] Push `vcr.vngcloud.vn/111480-abp111646/pyannote:0.1`
- [ ] Deploy Agent Runtime `runtime-s2-general-4x8` (4 CPU, 8 GB)
- [ ] Env vars trong AgentBase: `SERVER_TOKEN` (random hex), `HF_TOKEN`
- [ ] Test endpoint `/health` + `/diarize` qua public URL
- [ ] Wire `.env` Mee:
  - [ ] `PYANNOTE_REMOTE_URL=https://<endpoint>.agentbase.vngcloud.vn`
  - [ ] `PYANNOTE_REMOTE_TOKEN=<SERVER_TOKEN>`
- [ ] Hybrid routing trong `meeting/services/local_diarize.py`:
  - [ ] Audio < 5 phút → AgentBase CPU
  - [ ] Audio > 5 phút → Kaggle GPU
  - [ ] Cả 2 unreachable → local CPU
- [ ] Email VNG support hỏi GPU Agent Runtime / Custom container Endpoint roadmap

### ⚡ Easy wins (low effort, high impact)

- [ ] **num_speakers từ attendees** (suggestion #4) — pass `len(attendees)` vào pyannote pipeline → giảm over/under-clustering. 30-45 phút.
- [ ] **Silero VAD pre-Whisper** (suggestion #1) — strip silence trước STT → giảm hallucination ("Cảm ơn các bạn"). 1-2h. Lib: `silero-vad`.
- [ ] **Lọc micro-segments < 1s** (suggestion #5) — merge với neighbor cùng speaker, drop khác speaker. 1h.
- [x] **Fix `/import-transcript` race** — cleaner re-run mỗi click Gen MoM (fixed 2026-06-10, transcript_changed guard)

### 🧪 Tests cho race conditions vừa fix

**Context**: 6+ race conditions fixed gần đây, 0 test. Sẽ regress khi refactor sau.

- [ ] `meeting/api/test_import_transcript.py` — assert cleaner KHÔNG trigger khi text không đổi
- [ ] `meeting/graphs/test_mom_graph.py` — assert MoM `wait_for_inflight` khi cleaner còn chạy
- [ ] `meeting/services/test_parallel_diarize.py` — assert global re-ID merge correct
- [ ] Setup `pytest` config + 1 fixture cho test DB
- [ ] Add to CI later

---

## P1 — Next 2-4 weeks

### 🏗️ Pipeline refactor (senior critique #1)

**Why**: `recording.clean_segments` đang là "junk drawer" chứa segments + edited + cluster_mapping. Cleaner LLM làm 4 việc lúc. → race condition liên miên, hard to test.

- [ ] DB migration tách field:
  - [ ] `recording.transcribe_segments` (Whisper raw)
  - [ ] `recording.diarize_turns` (pyannote raw)
  - [ ] `recording.speaker_mapping` (single source: cluster → name)
  - [ ] `recording.cleaned_segments` (cleaner output only)
- [ ] State machine: STT → diarize → speaker_id → clean → MoM
- [ ] Mỗi stage 1 schema input/output cố định
- [ ] Cleaner KHÔNG sửa speaker_mapping; speaker_matcher service riêng

### 👥 Speaker ID upgrade (senior critique #4)

**Why**: Complaint #1 — wrong speaker labels. Multiple paths to cluster_mapping → conflict.

- [ ] Voice enrollment matching (depends P0 voice enrollment)
- [ ] Lower cosine threshold 0.30 → **0.25**
- [ ] Top-2 margin check: chỉ accept nếu top1 << top2 (delta > 0.1)
- [ ] Dedup logic: 2 clusters match same name → keep cluster với highest similarity
- [ ] Remove cleaner LLM speaker inference (cleaner chỉ fix text)

### 🛡️ MaaS resilience (senior critique #5)

**Why**: Toàn bộ system die khi VNG MaaS có incident. Không retry/circuit breaker.

- [ ] `meeting/services/llm_client.py` abstraction
  - [ ] Retry với exponential backoff (3 attempts, 1s/2s/4s)
  - [ ] Circuit breaker (fail-fast khi 5xx liên tục)
  - [ ] Cost tracking per call → `recording.cost_usd`
- [ ] Per-call fallback chain: Qwen → Gemma → GPT-OSS → Ollama local
- [ ] Rate limit tracker với UI banner "Qwen quota 45/50 today"

### 📊 Observability (senior critique #6)

**Why**: Debug bug khi system phức tạp = đọc log line by line. Không bền.

- [ ] Structured JSON logs: `logger.info(json.dumps({event, recording_id, stage, latency_ms}))`
- [ ] `recording.pipeline_events` JSONB array — append mỗi stage start/end
- [ ] Timeline UI panel: render mỗi recording's stages với duration bars
- [ ] (Optional) Prometheus exporter for `/metrics`

### ⚛️ React migration Phase D

**Why**: Vanilla JS đã ở giới hạn maintainability. window globals + race conditions.

- [ ] Migrate ChatPane sang React component
- [ ] HITL action card (Approve/Reject) trong React
- [ ] State management với Zustand (thay window globals)
- [ ] Auth guard component
- [ ] Polish overall (loading states, error boundaries)

---

## P2 — Backlog

### Improvements không block release

- [ ] Audio enhancement (noise reduction, loudnorm) — toggle, off by default
- [ ] faster-whisper self-host (3-4x speedup, drop-in cho MaaS Whisper)
- [ ] Async/sync DB engine consolidation
- [ ] Vietnamese normalizer service (gom vocab + phonetic + diacritics)
- [ ] Transcript edit feature polish (task #11)
- [ ] Multi-tenant refactor (cho Teams integration)

### From senior critique

- [ ] `recording` table mỗi JSONB col có Pydantic schema (data integrity)
- [ ] Health check / readiness probe endpoint cho k8s
- [ ] Env var validation on startup
- [ ] Secrets vault thay `.env` plain

---

## P3 — Vision (post-hackathon)

### 🤖 Teams/Zoom integration (otter.ai style)

- [ ] OAuth Google Calendar / Microsoft 365
- [ ] Calendar poller (Celery beat, 5 phút/lần)
- [ ] Bot user join meeting:
  - [ ] **Recall.ai** ($75/100 meetings) — easiest
  - [ ] Self-host open-source (Vexa, Bot.Meet) — 1-2 tuần
  - [ ] Headless Chrome + Puppeteer — fragile
- [ ] Slack/Teams webhook output (post MoM to channel)

### 🌐 Browser extension

- [ ] Chrome manifest v3 + content script
- [ ] `chrome.tabCapture` cho tab audio
- [ ] WebSocket binary → Mee `/ws/live`
- [ ] Auth flow extension storage
- [ ] Chrome Web Store submission

### 🚀 Other R&D

- [ ] whisper-streaming sliding window (live transcription smooth)
- [ ] Production deployment full (Modal/Runpod/self-host)
- [ ] Cost optimization (caching, batching)
- [ ] Mobile app (React Native)

---

## Done

> History of significant completed items with context.

### Sprint 04 (current — 2026-06-10)

- [x] **Background task infrastructure** — Celery + RabbitMQ replacing `asyncio.to_thread`
- [x] **3 tasks moved to Celery**: `gen_mom_task`, `clean_recording_task`, `diarize_recording_task`
- [x] **Async/Sync DB refactor (Option 1)** — 2/3 tasks dùng sync DB → 0 event-loop binding bugs
- [x] **Parallel pyannote diarize** — chunked + global re-ID via AHC clustering
- [x] **Inline pyannote in chunked transcribe (A1)** — eliminate cleaner race
- [x] **Kaggle GPU pyannote server** — cloudflared tunnel, drop-in remote
- [x] **Vocab learning từ user edits** — cross-project pool
- [x] **Speaker voice preview** — 3s WAV samples per cluster trong SpeakerMapper
- [x] **Fix chunked transcribe** — repetition guard (word + sentence + degenerate)
- [x] **Build pyannote Docker image** — 4 bug fixes (python3.11, hf_hub<1.0, use_auth_token, port 8080)
- [x] **Push image to VNG Container Registry** — vcr.vngcloud.vn/111480-abp111646/pyannote:0.1
- [x] **Fix `/import-transcript` race condition** — cleaner KHÔNG re-run mỗi Gen MoM khi transcript không đổi
- [x] **Dev scripts** — `scripts/{db,rabbitmq,backend,celery,frontend,stop,status}.sh`
- [x] **Architecture docs (Obsidian)** — 10 mermaid diagrams + Architecture Current v0.5
- [x] **Progress report slides** — `slides/progress_2026-06-10.html` (reveal.js)

### Pre-sprint 04 (from task list)

- [x] Memory persistence (replace stub)
- [x] Few-shot prompt enhance (commitment/blocker/decision)
- [x] Speaker ID light (LLM-based, no diarization) — superseded by pyannote
- [x] Clean transcript view (Phase D)
- [x] SpkID Phase 1-4 (voiceprints schema, embeddings, matching, FE UI)
- [x] Dynamic phonetic generation
- [x] Multi-model selector (STT + LLM per recording/meeting)
- [x] MoM language picker (UI default + per-recording override)
- [x] React migration Phase A, B, C

---

## Decisions log

> Architectural decisions + lý do. Append-only.

### 2026-06-10 — Voice enrollment slogan
- **Decision**: GreenNode slogan as enrollment script
  - VI: `AI Cloud hiệu năng cao dành riêng cho doanh nghiệp số`
  - EN: `High performance AI Cloud for digital-native business`
- **Why**: ~15-30s combined, cover VN+EN code-switching, đủ robust cho wespeaker
- **Apply**: Hardcoded trong `/onboard/voice` page. User chỉ enroll 1 lần.

### 2026-06-10 — Mock O365 design
- **Decision**: Full mimic Microsoft login UI + landing page trước
- **Why**: Demo polish cho judges (trông như tích hợp thật)
- **Apply**: Mock chỉ dùng dev/demo, banner "MOCK" trên top. Real O365 swap qua env var.

### 2026-06-10 — Working plan in PLAN.md
- **Decision**: `PLAN.md` ở root là single source of truth, working document
- **Why**: Survives conversation compaction, easy to checkbox, decisions logged
- **Apply**: Update mỗi sprint completion. Future Claude đọc trước khi propose work.

### 2026-06-10 — AgentBase pyannote deploy via Agent Runtime
- **Decision**: Agent Runtime CPU (4 CPU 8 GB) thay vì Endpoint
- **Why**: Endpoint chỉ support vLLM pre-built; Custom container greyed-out. Agent Runtime cho custom Docker nhưng chỉ CPU.
- **Trade-off**: Pyannote chạy CPU sẽ chậm (5 min audio = 3-8 min). Accept cho hackathon, hybrid với Kaggle GPU cho audio dài.
- **Apply**: Email VNG hỏi GPU Agent Runtime / Custom Endpoint. Migrate khi enable.

### 2026-06-10 — Pyannote container port 8080 + /health
- **Decision**: Container listen 8080 (không 8000) + endpoint `/health`
- **Why**: AgentBase Agent Runtime yêu cầu cứng — không override được
- **Apply**: Dockerfile EXPOSE 8080 + CMD --port 8080. pyannote_server.py có cả `/` và `/health`.

### 2026-06-10 — Pyannote dependency pin huggingface_hub<1.0
- **Decision**: Pin `huggingface_hub<1.0` trong Dockerfile
- **Why**: hf_hub 1.x drop `use_auth_token` kwarg, pyannote 3.4 còn dùng → break
- **Apply**: Khi pyannote 3.5+ release với token= API → có thể bỏ pin

### 2026-06-10 — Canonical project folder
- **Decision**: `/home/lap15466/greennode/mee-meeting-agent/` (KHÔNG có `-master`)
- **Why**: `-master` là zip snapshot cũ, không build/run từ đó
- **Apply**: Mọi Read/Write/Bash dùng path non-`-master`
- **Memory**: `~/.claude/projects/.../memory/feedback_project_folder.md`

### 2026-06-10 — Celery sync DB refactor (Option 1)
- **Decision**: 2/3 Celery tasks (`clean`, `diarize`) refactor sang sync DB (psycopg2)
- **Why**: asyncpg pool bound to event loop. Khi Celery solo pool spawn task mới với loop mới → pool dùng loop cũ chết → `RuntimeError: Future attached to different loop`
- **Trade-off**: Duplicate repository code (async + sync mirrors). `gen_mom_task` vẫn async vì LangGraph checkpointer require async.
- **Apply**: Future tasks → sync DB by default. Chỉ async khi cần LangGraph.

### Earlier (from memory)

- **MoM 2-level design**: Per-recording (`recordings.mom_json`) + project summary timeline decisions (`meetings.project_summary_json`)
- **docs-agent NOT used**: User confused with pm-agent. A2A deferred indefinitely.

---

## Reference

### Project structure
```
mee-meeting-agent/
├── meeting/                # Backend (FastAPI + Celery)
│   ├── api/                # HTTP endpoints
│   ├── auth/               # [P0 NEW] Auth provider abstraction
│   ├── db/                 # SQLAlchemy models + sync + async sessions
│   ├── graphs/             # LangGraph (MoM, chat)
│   ├── services/           # Business logic (cleaner, diarize, etc.)
│   ├── celery_app.py       # Celery config
│   ├── tasks.py            # Celery task definitions
│   └── app.py              # FastAPI app
├── meeting_frontend/       # Vanilla JS FE (served by FastAPI)
├── meeting_frontend_react/ # React + Vite (in migration)
├── alembic/                # DB migrations
├── tools/kaggle/           # Pyannote remote server
├── scripts/                # Dev scripts
├── docs/                   # Architecture docs (separate from Obsidian)
└── PLAN.md                 # ← this file
```

### External services
| Service | URL | Purpose |
|---|---|---|
| VNG MaaS | `maas-llm-aiplatform-hcm.api.vngcloud.vn` | Whisper, Qwen, Gemma, GPT-OSS, bge-m3 |
| VNG Container Registry | `vcr.vngcloud.vn/111480-abp111646` | Pyannote Docker image |
| AgentBase Agent Runtime | TBD after deploy | Pyannote inference (CPU) |
| Kaggle T4 GPU | Cloudflared tunnel (random URL) | Pyannote fallback (GPU free) |
| HuggingFace | huggingface.co | Pyannote model weights |
| Postgres | `localhost:5435` (Docker) | DB + pgvector |
| RabbitMQ | `localhost:5672` (Docker) | Celery broker |

### Memory files
- `~/.claude/projects/-home-lap15466-greennode-mee-meeting-agent-master/memory/`
  - `MEMORY.md` — index
  - `user_role.md` — User mới với AI Agents, hướng dẫn step-by-step
  - `project_task.md` — R&D AI Meeting Agent context
  - `mom_two_level.md` — MoM 2-level design
  - `feedback_project_folder.md` — Canonical folder rule
  - `docs_agent_integration.md` — docs-agent excluded

### Architecture docs (Obsidian vault)
- `~/greennode/GreenNode/Meeting Agent/02 Kiến trúc & Design/`
  - `Architecture Current v0.5 (post-Celery sync).md` — 588 lines, 10 mermaid diagrams
  - `Deployment Diagram Explained.md`
  - `DB Schema 3-cap.md`
  - `HITL Pattern.md`

### Slides / reports
- `slides/progress_2026-06-10.html` — Sprint 04 report (reveal.js + mermaid)

### Key git commits
(populate over time)

---

## Backlog graveyard 🪦

> Things explicitly deferred indefinitely. Mỗi item có lý do.

- [-] **docs-agent integration** — User confused với pm-agent. Removed from scope.
- [-] **A2A protocol** — Defer indefinitely, không phù hợp hackathon timeline.
- [-] **Triton Python backend cho pyannote** — 1-2 ngày refactor, không đáng cho hackathon. Revisit nếu cần scale production.
