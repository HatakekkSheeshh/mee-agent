#!/usr/bin/env bash
# Start Postgres + Adminer (idempotent, runs in background via docker compose).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ Starting Postgres + Adminer…"
docker compose up -d postgres adminer

echo
echo "Postgres : postgresql://mee@localhost:5435/mee"
echo "Adminer  : http://localhost:8080  (server=postgres, db=mee, user=mee)"
echo
docker compose ps postgres adminer
