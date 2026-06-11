#!/usr/bin/env bash
# Start RabbitMQ broker for Celery (idempotent, runs in background).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ Starting RabbitMQ…"
docker compose up -d rabbitmq

# Wait until broker accepts connections (max ~20s)
echo -n "→ Waiting for broker to be ready"
for _ in {1..20}; do
    if docker exec mee-rabbitmq rabbitmq-diagnostics -q ping >/dev/null 2>&1; then
        echo " ✓"
        break
    fi
    echo -n "."
    sleep 1
done

echo
echo "AMQP        : amqp://mee@localhost:5672//"
echo "Management  : http://localhost:15672  (user: mee / pass: mee_dev_password)"
echo
docker compose ps rabbitmq
