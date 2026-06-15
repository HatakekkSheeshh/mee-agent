#!/usr/bin/env bash
# Cron wrapper for the background role auto-classify worker.
#
# Why a wrapper: cron runs with a minimal environment and an arbitrary CWD, so
# we pin the project root here (the worker's own load_dotenv then reads .env
# from this dir) and append timestamped output to a logfile. Keeping the cron
# line itself short + the env handling in one place makes it easy to audit.
#
# Install (every 15 minutes, adjust to taste) — run `crontab -e` and add:
#   */15 * * * * /home/lap14734/projects/mee-meeting-agent/scripts/classify_roles_cron.sh
#
# Dry-run from cron (writes nothing, just logs decisions): pass --dry-run, e.g.
#   */15 * * * * /home/.../scripts/classify_roles_cron.sh --dry-run
#
# See docs/superpowers/specs/2026-06-15-role-autoclassify-design.md
set -euo pipefail
cd "$(dirname "$0")/.."

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# venv/ is the active virtualenv in this project (not .venv/ — see CLAUDE.md).
exec >>"$LOG_DIR/classify_roles.log" 2>&1
echo "===== $(date -Is) classify_roles run (args: $*) ====="
exec venv/bin/python scripts/classify_roles.py "$@"
