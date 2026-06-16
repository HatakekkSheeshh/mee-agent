#!/usr/bin/env bash
# One image, two roles — AgentBase runs a single CMD per runtime, so the
# role is chosen at runtime via the MEE_ROLE env var:
#
#   (unset) / MEE_ROLE=web  → API server only. This runtime receives user
#                             traffic and DISPATCHES heavy jobs to the queue
#                             (it does NOT run a worker, so requests stay fast).
#   MEE_ROLE=worker         → Celery worker that CONSUMES the queue (gen MoM,
#                             clean, diarize). It also starts the API on :8080
#                             so AgentBase's health check passes (a bare worker
#                             never binds 8080 → runtime would go ERROR).
#
# Both roles need CELERY_BROKER_URL pointing at the shared broker (RabbitMQ).
# If the broker is unreachable, the web role degrades to the in-process
# fallback (still correct after the s2.commit() fix) — it just won't scale.
set -e

if [ "${MEE_ROLE:-web}" = "worker" ]; then
    echo "[start.sh] MEE_ROLE=worker → launching Celery worker"
    celery -A meeting.celery_app worker \
        --pool="${CELERY_POOL:-prefork}" \
        --concurrency="${CELERY_CONCURRENCY:-4}" \
        --loglevel="${CELERY_LOGLEVEL:-info}" &
fi

# API server (foreground) — the container lives/dies with it. On the worker
# runtime this just satisfies the :8080 health check; no traffic is routed here.
exec python main.py
