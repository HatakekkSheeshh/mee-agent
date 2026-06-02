# Mee Frontend — React rewrite

Vite + React 18 + TypeScript + Tailwind CSS.

## Setup

```bash
cd meeting_frontend_react
npm install
npm run dev
```

Dev server runs at http://localhost:5173. Vite proxies:
- `/api/*` → `http://localhost:8001` (FastAPI backend — make sure `python run_meeting.py` is running)
- `/ws` → `ws://localhost:9091` (live transcribe WebSocket)

## Folder structure

```
src/
├── api/client.ts        # Typed fetch wrapper for all backend endpoints
├── types/api.ts         # TypeScript types matching backend responses
├── store/AppContext.tsx # Global state (meetings list, current selection)
├── components/
│   ├── Sidebar.tsx          # Projects list (✅ Phase A done)
│   ├── TranscriptPane.tsx   # Record + upload + transcript (Phase B)
│   ├── MoMPane.tsx          # MoM + project summary display (Phase C)
│   └── ChatPane.tsx         # HITL chat (Phase D)
├── App.tsx
├── main.tsx
└── index.css            # Tailwind base + component classes
```

## Migration phases

| Phase | Scope | Status |
|---|---|---|
| A | Vite scaffold, types, API client, AppContext, Sidebar | ✅ done |
| B | TranscriptPane: record (WebSocket), upload, raw/clean toggle | ⏳ next |
| C | MoMPane: render MoM + project summary timeline + download | ⏳ |
| D | ChatPane HITL with approve/reject + toasts + polish | ⏳ |

## Build for production

```bash
npm run build      # outputs to dist/
npm run preview    # smoke-test the build
```

Backend can serve `dist/` via `StaticFiles` to host the SPA alongside FastAPI.
