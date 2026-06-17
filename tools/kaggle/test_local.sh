#!/usr/bin/env bash
# Smoke test the pyannote container running locally.
# Usage:
#   tools/kaggle/test_local.sh /path/to/short_audio.wav
#   tools/kaggle/test_local.sh                    # uses first WAV in assets/
#
# Expects:
#   - Container running at http://localhost:8000
#   - SERVER_TOKEN env var matching what container was started with
#     (default test-local-123, same as the run command in instructions)
set -uo pipefail

TOKEN="${SERVER_TOKEN:-test-local-123}"
URL="${URL:-http://localhost:8080}"

# Pick audio file
AUDIO="${1:-}"
if [[ -z "$AUDIO" ]]; then
    AUDIO=$(find "$(dirname "$0")/../../assets" -maxdepth 1 -name "*.wav" -size -10M 2>/dev/null | head -1)
    [[ -z "$AUDIO" ]] && AUDIO=$(find "$(dirname "$0")/../../assets" -maxdepth 1 -name "*.flac" -size -10M 2>/dev/null | head -1)
fi
if [[ -z "$AUDIO" ]] || [[ ! -f "$AUDIO" ]]; then
    echo "✗ no audio file. Pass path as arg: $0 /path/to/audio.wav"
    exit 1
fi
echo "Testing with: $AUDIO ($(du -h "$AUDIO" | cut -f1))"

# 1. Health check
echo
echo "→ Health check"
HEALTH=$(curl -fsS "$URL/" 2>&1) || { echo "✗ health failed: $HEALTH"; exit 1; }
echo "$HEALTH"

# 2. Auth check — should 401 without token
echo
echo "→ Auth check (no token → expect 401)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$URL/diarize" -F "file=@$AUDIO")
if [[ "$CODE" == "401" ]]; then
    echo "✓ 401 returned as expected"
else
    echo "✗ expected 401, got $CODE — auth not enforced!"
fi

# 3. Diarize (real call)
echo
echo "→ Diarize call (SERVER_TOKEN=$TOKEN)"
echo "   This may take 30s-3min on CPU depending on audio length…"
RESP_FILE=$(mktemp)
trap "rm -f $RESP_FILE" EXIT
START=$(date +%s)
curl -fsS -X POST "$URL/diarize" \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@$AUDIO" -o "$RESP_FILE" \
    || { echo "✗ diarize failed"; exit 1; }
DUR=$(($(date +%s) - START))

# Pretty-print summary — read JSON from temp file so embedding floats +
# base64 audio (often 50-500 KB total) don't exceed shell ARG_MAX.
echo "✓ diarize done in ${DUR}s ($(du -h "$RESP_FILE" | cut -f1) response)"
RESP_FILE="$RESP_FILE" python3 <<'PYEOF'
import json, os
with open(os.environ["RESP_FILE"]) as f:
    r = json.load(f)
turns = r.get("turns", [])
emb = r.get("cluster_embeddings", {})
samples = r.get("sample_audio_b64", {})
print(f"  turns          : {len(turns)}")
print(f"  speakers       : {len(set(t['speaker'] for t in turns))}")
print(f"  embeddings     : {len(emb)} clusters x {len(next(iter(emb.values()))) if emb else 0} dims")
print(f"  sample_audio   : {len(samples)} clips")
if turns[:3]:
    print(f"  first 3 turns  :")
    for t in turns[:3]:
        print(f"    {t['start']:6.1f}s → {t['end']:6.1f}s  [{t['speaker']}]")
PYEOF
