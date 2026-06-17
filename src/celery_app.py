"""Celery app configuration for background MoM generation.

Why Celery instead of asyncio.to_thread:
  - Persistence: tasks survive backend restart (broker queues them durably)
  - Auto-retry: transient LLM failures retry with backoff (configurable)
  - Monitor: Flower web UI at http://localhost:5555 shows queue depth,
    success/fail rate, per-task duration
  - Scale: add more worker processes/machines without code changes

Broker = RabbitMQ (AMQP queue). Result backend also via RabbitMQ's RPC mode
(`rpc://`) — saves a separate Redis/DB just for storing results.

Env vars (defaults match docker-compose.yml RabbitMQ service):
  CELERY_BROKER_URL    — default amqp://mee:mee_dev_password@localhost:5672//
  CELERY_RESULT_BACKEND — default rpc://

Start worker (already wired into run_meeting.py):
  celery -A src.celery_app worker --loglevel=info --concurrency=4
"""
from __future__ import annotations

import os
from pathlib import Path

# Load .env so `python -m celery -A src.celery_app worker` works without
# going through run_meeting.py (which load_dotenv()'s before importing).
# Searches upward from this file: src/ → project root.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.is_file():
        load_dotenv(_env_path, override=False)
except ImportError:
    pass  # dotenv optional — env vars may be set by shell / systemd / docker

from celery import Celery


def _default_broker_url() -> str:
    user = os.getenv("RABBITMQ_USER", "mee")
    pwd = os.getenv("RABBITMQ_PASSWORD", "mee_dev_password")
    host = os.getenv("RABBITMQ_HOST", "localhost")
    port = os.getenv("RABBITMQ_PORT", "5672")
    return f"amqp://{user}:{pwd}@{host}:{port}//"


CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL") or _default_broker_url()
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "rpc://")


celery_app = Celery(
    "mee",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["src.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    # Result lifetime — 1h is enough for FE to poll + cache. Longer = bigger
    # broker memory footprint.
    result_expires=3600,
    # Show STARTED state (default just PENDING → SUCCESS/FAILURE). Lets FE
    # distinguish "queued, not picked up yet" vs "worker is processing".
    task_track_started=True,
    # MoM gen typically 1-3 min. Hard-kill at 15 min (avoid infinite stuck
    # tasks if LLM hangs). Soft warn at 12 min — task can catch SoftTimeLimit
    # exception and clean up.
    task_time_limit=900,
    task_soft_time_limit=720,
    # Prefork pool default: spawns worker subprocesses. concurrency=4 →
    # 4 LLM gens can run in parallel per worker process. Override via
    # `--concurrency=N` flag at worker start.
    worker_prefetch_multiplier=1,  # one task per worker at a time (fairer)
    task_acks_late=True,            # ack only after task completes — safer for retry
)


def is_broker_reachable(timeout: float = 1.0) -> bool:
    """Quick TCP probe to RabbitMQ broker. Used by /api/recordings/{id}/generate-mom
    endpoint to decide between Celery path (broker up) and asyncio fallback
    (broker down — dev convenience)."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(CELERY_BROKER_URL.replace("amqp://", "http://"))
    host = parsed.hostname or "localhost"
    port = parsed.port or 5672
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass
