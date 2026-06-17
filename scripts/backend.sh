#!/usr/bin/env bash
# Start FastAPI backend WITHOUT auto-starting Celery worker.
# Run scripts/celery.sh in a separate terminal for background tasks.
#
# Why split: dual file-watcher (uvicorn --reload + watchmedo) saturates a CPU
# core on idle. Running them as separate processes lets you Ctrl+C each
# independently and keeps logs uncluttered.
set -euo pipefail
cd "$(dirname "$0")/.."

# Activate venv if not already active
if [[ -z "${VIRTUAL_ENV:-}" ]] && [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Pre-flight: Postgres must be up (alembic + asyncpg connect on startup)
if ! docker exec mee-postgres pg_isready -U mee >/dev/null 2>&1; then
    echo "⚠  Postgres not running. Start it: scripts/db.sh"
    exit 1
fi

echo "→ Starting FastAPI (no Celery)…"
echo "   http://localhost:8000  →  static FE + API"
echo
exec python run_meeting.py --no-celery
