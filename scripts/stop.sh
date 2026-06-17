#!/usr/bin/env bash
# Stop FastAPI + Celery (Python processes). Docker services (Postgres,
# RabbitMQ, Adminer) keep running — stop them manually with `docker compose stop`.
set -uo pipefail
cd "$(dirname "$0")/.."

stop() {
    local label="$1" pattern="$2"
    local pids
    pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    if [[ -z "$pids" ]]; then
        echo "  $label : not running"
        return
    fi
    echo "  $label : killing PIDs $pids"
    kill $pids 2>/dev/null || true
    sleep 1
    # Force-kill survivors
    pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
}

echo "→ Stopping Mee dev processes:"
stop "Celery     " "celery.*src.celery_app"
stop "Watchmedo  " "watchmedo.*celery"
stop "FastAPI    " "run_meeting.py"
stop "Uvicorn    " "uvicorn.*meeting"
stop "Vite       " "vite.*frontend"
echo
echo "Docker services (postgres, rabbitmq, adminer) still running."
echo "To stop them too: docker compose stop"
