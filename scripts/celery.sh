#!/usr/bin/env bash
# Start Celery worker standalone — no watchmedo wrapper, so only ONE file
# watcher runs in the dev environment (uvicorn's reload handles meeting/*.py).
# To pick up changes in meeting/tasks.py: Ctrl+C and re-run this script.
#
# Pool default = solo (1 task at a time, fastest startup, no async event-loop
# binding issues). Override with CELERY_POOL=prefork CELERY_CONCURRENCY=4 ./celery.sh
# for production-like parallelism — see meeting/tasks.py for safety notes.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${VIRTUAL_ENV:-}" ]] && [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Source .env so shell-level vars (CELERY_POOL, CELERY_CONCURRENCY, etc.)
# are available to the celery worker CLI args below. Without this, .env values
# only reach Python after celery_app.py's dotenv import — too late for the
# `--pool` / `--concurrency` flags which are set BEFORE Python starts.
# `set +u` while sourcing so passwords containing $-sigils (e.g. `$H` inside
# a DATABASE_URL password) don't crash with "unbound variable".
if [[ -f .env ]]; then
    set +u
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
    set -u
fi

# Pre-flight: RabbitMQ must be reachable
if ! docker exec mee-rabbitmq rabbitmq-diagnostics -q ping >/dev/null 2>&1; then
    echo "⚠  RabbitMQ not running. Start it: scripts/rabbitmq.sh"
    exit 1
fi

POOL="${CELERY_POOL:-solo}"
CONCURRENCY="${CELERY_CONCURRENCY:-2}"
LOGLEVEL="${CELERY_LOGLEVEL:-info}"

echo "→ Starting Celery worker  (pool=$POOL, loglevel=$LOGLEVEL)"
echo "   tasks: gen_mom · clean_recording · diarize_recording"
echo

if [[ "$POOL" == "solo" ]]; then
    exec python -m celery -A meeting.celery_app worker \
        --pool="$POOL" \
        --loglevel="$LOGLEVEL"
else
    exec python -m celery -A meeting.celery_app worker \
        --pool="$POOL" \
        --concurrency="$CONCURRENCY" \
        --loglevel="$LOGLEVEL"
fi
