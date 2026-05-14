#!/usr/bin/env bash
# ================================================================
# deploy.sh — Build, push & redeploy Mee Meeting Note Agent
# Chạy script này trên máy local (cần Docker + curl + jq).
# ================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# On Windows the App Store python3 alias exits 49 without a terminal; fall back to real python
if ! python3 -c "" 2>/dev/null; then
  _REAL_PY="$(command -v python 2>/dev/null || true)"
  [ -z "$_REAL_PY" ] && _REAL_PY="/c/Users/LAP15269/AppData/Local/Programs/Python/Python314/python.exe"
  python3() { "$_REAL_PY" "$@"; }
  export -f python3
fi

# ── Màu sắc ───────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
log()  { echo -e "${CYAN}[deploy]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ── Cấu hình từ file có sẵn ───────────────────────────────────
GREENNODE_JSON=".greennode.json"
VCR_CREDS=".agentbase/vcr-creds.json"
ENV_FILE=".env"
RUNTIME_ID="runtime-a67d83e4-6075-4241-95c5-0366a657374a"
FLAVOR_ID="1x1-general"

IAM_TOKEN_URL="https://iam.api.vngcloud.vn/accounts-api/v2/auth/token"
RUNTIME_URL="https://agentbase.api.vngcloud.vn/runtime/agent-runtimes"

# ── Đọc credentials ───────────────────────────────────────────
[ -f "$GREENNODE_JSON" ] || err "Không tìm thấy $GREENNODE_JSON"
[ -f "$VCR_CREDS"      ] || err "Không tìm thấy $VCR_CREDS"
[ -f "$ENV_FILE"       ] || err "Không tìm thấy $ENV_FILE"

CLIENT_ID=$(python3 -c "import json; print(json.load(open('$GREENNODE_JSON'))['client_id'])")
CLIENT_SECRET=$(python3 -c "import json; print(json.load(open('$GREENNODE_JSON'))['client_secret'])")
VCR_USER=$(python3 -c "import json; print(json.load(open('$VCR_CREDS'))['username'])")
VCR_PASS=$(python3 -c "import json; print(json.load(open('$VCR_CREDS'))['password'])")
VCR_REGISTRY=$(python3 -c "import json; print(json.load(open('$VCR_CREDS'))['registry'])")
VCR_REPO=$(python3 -c "import json; print(json.load(open('$VCR_CREDS'))['repository'])")

IMAGE_NAME="mee-agent"
TAG="v$(date +%Y%m%d%H%M%S)"
FULL_IMAGE="${VCR_REGISTRY}/${VCR_REPO}/${IMAGE_NAME}:${TAG}"

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Mee Agent — Deploy Pipeline${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "  Image  : ${CYAN}${FULL_IMAGE}${NC}"
echo -e "  Runtime: ${CYAN}${RUNTIME_ID}${NC}"
echo -e "  Flavor : ${CYAN}${FLAVOR_ID}${NC}"
echo ""

# ── Step 1: Lấy IAM token ─────────────────────────────────────
log "Step 1/5 — Lấy IAM token..."
TOKEN_RESP=$(curl -sf -X POST "$IAM_TOKEN_URL" \
  -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d "grant_type=client_credentials" \
  -H "Content-Type: application/x-www-form-urlencoded")
IAM_TOKEN=$(echo "$TOKEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
[ -n "$IAM_TOKEN" ] || err "Không lấy được IAM token"
ok "IAM token: ${IAM_TOKEN:0:25}..."

# ── Step 2: Build Docker image ────────────────────────────────
log "Step 2/5 — Build Docker image (linux/amd64)..."
docker build --platform linux/amd64 -t "$FULL_IMAGE" . \
  || err "Docker build thất bại"
ok "Build xong: $FULL_IMAGE"

# ── Step 3: Login & Push lên VCR ──────────────────────────────
log "Step 3/5 — Login vào VCR và push image..."
echo "$VCR_PASS" | docker login "$VCR_REGISTRY" -u "$VCR_USER" --password-stdin \
  || err "Docker login thất bại"
docker push "$FULL_IMAGE" \
  || err "Docker push thất bại"
ok "Push xong: $FULL_IMAGE"

# ── Step 4: Build env payload & gọi API update runtime ────────
log "Step 4/5 — Update runtime với image mới..."

# Đọc .env thành JSON {"KEY": "VALUE", ...}
ENV_JSON=$(python3 - <<'PYEOF'
import os, json

env = {}
with open(".env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            # Bỏ qua các biến auto-inject của AgentBase
            skip = {"GREENNODE_CLIENT_ID","GREENNODE_CLIENT_SECRET",
                    "GREENNODE_AGENT_IDENTITY","GREENNODE_ENDPOINT_URL"}
            if k.strip() not in skip:
                env[k.strip()] = v.strip()
print(json.dumps(env))
PYEOF
)

# VCR imageAuth
IMAGE_AUTH=$(python3 -c "
import json
creds = json.load(open('.agentbase/vcr-creds.json'))
print(json.dumps({
    'enabled': True,
    'username': creds['username'],
    'password': creds['password']
}))
")

PAYLOAD=$(python3 - <<PYEOF
import json, sys

env_json = $ENV_JSON
vcr = json.load(open('.agentbase/vcr-creds.json'))
image_auth = {"enabled": True, "username": vcr["username"], "password": vcr["password"]}

payload = {
    "imageUrl": "$FULL_IMAGE",
    "flavorId": "$FLAVOR_ID",
    "description": "",
    "command": [],
    "args": [],
    "environmentVariables": env_json,
    "autoscaling": {
        "minReplicas": 1,
        "maxReplicas": 1,
        "cpuUtilization": 50,
        "memoryUtilization": 50
    },
    "imageAuth": image_auth
}
print(json.dumps(payload))
PYEOF
)

UPDATE_RESP=$(curl -sf -X PATCH "${RUNTIME_URL}/${RUNTIME_ID}" \
  -H "Authorization: Bearer $IAM_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

NEW_VERSION=$(echo "$UPDATE_RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
v = d.get('version', d.get('data', {}).get('version', 'N/A'))
print(v)
" 2>/dev/null || echo "N/A")
ok "Runtime updated — version mới: $NEW_VERSION"

# ── Step 5: Polling đợi ACTIVE ────────────────────────────────
log "Step 5/5 — Đợi runtime ACTIVE (timeout 5 phút)..."
TIMEOUT=300
INTERVAL=10
ELAPSED=0

while [ $ELAPSED -lt $TIMEOUT ]; do
    STATUS=$(curl -sf "${RUNTIME_URL}/${RUNTIME_ID}" \
      -H "Authorization: Bearer $IAM_TOKEN" | \
      python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', d.get('data',{}).get('status','UNKNOWN')))" 2>/dev/null || echo "UNKNOWN")

    echo -n "    status: $STATUS (${ELAPSED}s)..."
    if [ "$STATUS" = "ACTIVE" ]; then
        echo ""
        ok "Runtime ACTIVE!"
        break
    elif [ "$STATUS" = "ERROR" ]; then
        echo ""
        err "Runtime trả về ERROR. Kiểm tra logs trên AgentBase Console."
    else
        echo " chờ ${INTERVAL}s"
        sleep $INTERVAL
        ELAPSED=$((ELAPSED + INTERVAL))
    fi
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    warn "Timeout sau ${TIMEOUT}s. Kiểm tra thủ công tại:"
    warn "https://aiplatform.console.vngcloud.vn/runtime"
fi

# ── Lấy endpoint URL ──────────────────────────────────────────
ENDPOINT_URL=$(curl -sf "${RUNTIME_URL}/${RUNTIME_ID}/endpoints" \
  -H "Authorization: Bearer $IAM_TOKEN" | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
items = d.get('data', {}).get('listData', [])
for ep in items:
    if ep.get('name') == 'DEFAULT':
        print(ep.get('url',''))
        break
" 2>/dev/null || echo "")

# ── Health check ──────────────────────────────────────────────
HEALTH_CODE=""
if [ -n "$ENDPOINT_URL" ]; then
    HEALTH_CODE=$(curl -sf -o /dev/null -w "%{http_code}" "${ENDPOINT_URL}/health" 2>/dev/null || echo "000")
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${BOLD}  ✅ Deploy hoàn tất!${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "  Image   : ${GREEN}${FULL_IMAGE}${NC}"
echo -e "  Runtime : ${GREEN}${RUNTIME_ID}${NC}"
echo -e "  Version : ${GREEN}${NEW_VERSION}${NC}"
if [ -n "$ENDPOINT_URL" ]; then
    echo -e "  Endpoint: ${CYAN}${ENDPOINT_URL}${NC}"
    if [ "$HEALTH_CODE" = "200" ]; then
        echo -e "  Health  : ${GREEN}✓ OK (200)${NC}"
    else
        echo -e "  Health  : ${YELLOW}⚠ $HEALTH_CODE (container có thể đang warmup)${NC}"
    fi
fi
echo ""
echo -e "  Console : https://aiplatform.console.vngcloud.vn/runtime"
echo ""
