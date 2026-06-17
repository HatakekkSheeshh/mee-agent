#!/usr/bin/env bash
# Run the Mee STT server on the 2080 box (foreground, or inside tmux).
# For persistence across SSH disconnects, prefer the systemd unit (see README).
#
#   tmux new -s stt          # so it survives logout
#   ./run.sh
#   # Ctrl+B then D to detach;  tmux attach -t stt to return
set -euo pipefail

cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .venv/bin/activate

: "${HF_TOKEN:?Set HF_TOKEN (pyannote ToS accepted) before running}"

export PORT="${PORT:-9100}"
# Both STT backends enabled, on-demand. To pin one (e.g. clean benchmark of
# faster-whisper only): STT_BACKENDS=faster_whisper PRELOAD_STT=faster_whisper ./run.sh
export STT_BACKENDS="${STT_BACKENDS:-faster_whisper,phowhisper}"

echo "Starting Mee STT server on :$PORT (backends: $STT_BACKENDS)"
exec python mee_stt_server.py
