#!/usr/bin/env bash
# Open an SSH tunnel from THIS (dev) box → the STT server on nhihb-gpu-2080.
# After this, the Mee backend reaches the server at http://localhost:9100.
#
#   ./tunnel.sh           # start the tunnel (background, persistent)
#   ./tunnel.sh stop      # tear it down
#   ./tunnel.sh status    # check if it's up
#
# Uses autossh if available (auto-reconnects on drop); falls back to plain ssh.
set -euo pipefail

HOST="${STT_HOST:-nhihb-gpu-2080}"
LOCAL_PORT="${LOCAL_PORT:-9100}"
REMOTE_PORT="${REMOTE_PORT:-9100}"
PIDFILE="/tmp/mee-stt-tunnel.pid"

cmd="${1:-start}"

case "$cmd" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "Tunnel already running (pid $(cat "$PIDFILE"))."; exit 0
    fi
    if command -v autossh >/dev/null 2>&1; then
      autossh -M 0 -f -N \
        -o "ServerAliveInterval=30" -o "ServerAliveCountMax=3" -o "ExitOnForwardFailure=yes" \
        -L "${LOCAL_PORT}:localhost:${REMOTE_PORT}" "$HOST"
      pgrep -n autossh > "$PIDFILE"
      echo "autossh tunnel up: localhost:${LOCAL_PORT} → ${HOST}:${REMOTE_PORT} (pid $(cat "$PIDFILE"))"
    else
      ssh -f -N -o "ServerAliveInterval=30" -o "ExitOnForwardFailure=yes" \
        -L "${LOCAL_PORT}:localhost:${REMOTE_PORT}" "$HOST"
      pgrep -nf "ssh -f -N.*${LOCAL_PORT}:localhost:${REMOTE_PORT}" > "$PIDFILE" || true
      echo "ssh tunnel up: localhost:${LOCAL_PORT} → ${HOST}:${REMOTE_PORT}"
      echo "(install autossh for auto-reconnect: sudo apt install autossh)"
    fi
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "Tunnel stopped."
    else
      echo "No tunnel pidfile."
    fi
    ;;
  status)
    if curl -fsS -m 5 "http://localhost:${LOCAL_PORT}/health" >/dev/null 2>&1; then
      echo "✓ Tunnel + server healthy at http://localhost:${LOCAL_PORT}"
      curl -fsS "http://localhost:${LOCAL_PORT}/health"
    else
      echo "✗ Not reachable on localhost:${LOCAL_PORT} (tunnel down or server off)."
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|status}"; exit 1 ;;
esac
