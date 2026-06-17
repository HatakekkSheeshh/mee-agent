#!/usr/bin/env bash
# Start React + Vite dev server (frontend/).
# Vanilla JS frontend (frontend/) is served by FastAPI directly —
# no separate process needed for that.
set -euo pipefail
cd "$(dirname "$0")/../frontend"

if [[ ! -d node_modules ]]; then
    echo "→ Installing npm deps (first-time)…"
    npm install
fi

echo "→ Starting Vite dev server…"
echo "   http://localhost:5173  →  React FE (proxies /api → :8000)"
echo
exec npm run dev
