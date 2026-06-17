#!/usr/bin/env bash
# Show what's currently running for the Mee stack.
set -uo pipefail
cd "$(dirname "$0")/.."

check() {
    local label="$1" pattern="$2" url="${3:-}"
    if pgrep -f "$pattern" >/dev/null 2>&1; then
        local pid
        pid=$(pgrep -f "$pattern" | head -1)
        printf "  %-12s ✓ running (PID %s)" "$label" "$pid"
        [[ -n "$url" ]] && printf "  →  %s" "$url"
        echo
    else
        printf "  %-12s ✗ stopped\n" "$label"
    fi
}

check_docker() {
    local label="$1" container="$2" url="${3:-}"
    if docker ps --format '{{.Names}}' | grep -q "^$container$"; then
        printf "  %-12s ✓ running" "$label"
        [[ -n "$url" ]] && printf "  →  %s" "$url"
        echo
    else
        printf "  %-12s ✗ stopped\n" "$label"
    fi
}

echo "Docker services:"
check_docker "Postgres"  "mee-postgres"  "postgresql://localhost:5435"
check_docker "RabbitMQ"  "mee-rabbitmq"  "http://localhost:15672"
check_docker "Adminer"   "mee-adminer"   "http://localhost:8080"

echo
echo "Python processes:"
check "FastAPI"  "run_meeting.py"            "http://localhost:8000"
check "Celery"   "celery.*meeting.celery_app"
check "Vite FE"  "vite.*meeting_frontend_react" "http://localhost:5173"
