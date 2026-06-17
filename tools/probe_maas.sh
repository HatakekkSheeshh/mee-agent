#!/usr/bin/env bash
# probe_maas.sh — Test which VNG MaaS models your API key can access.
#
# Usage:
#   ./tools/probe_maas.sh                           # uses .env (LLM_API_KEY)
#   ./tools/probe_maas.sh KEY=vn-xxx                # override key
#   ./tools/probe_maas.sh USER=user-12345           # override user prefix
#   ./tools/probe_maas.sh USER=user-12345 KEY=vn-yy
#
# Each line of MODELS below is a candidate <provider>/<model> slug. The script
# fires a tiny POST /chat/completions and prints HTTP status. 200 = accessible.
# Add/remove lines to test more candidates.

set -uo pipefail

BASE_HOST="${BASE_HOST:-https://maas-llm-aiplatform-hcm.api.vngcloud.vn}"

# Load .env if present
if [ -f "$(dirname "$0")/../.env" ]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "$(dirname "$0")/../.env" | xargs -d '\n' -I {} echo {} 2>/dev/null || true)
fi

# CLI overrides (KEY=... USER=...)
for arg in "$@"; do
  case "$arg" in
    KEY=*) LLM_API_KEY="${arg#KEY=}" ;;
    USER=*) MAAS_USER="${arg#USER=}" ;;
  esac
done

KEY="${LLM_API_KEY:-${WHISPER_API_KEY:-}}"
USER="${MAAS_USER:-user-53461}"

if [ -z "$KEY" ]; then
  echo "❌ No API key. Pass KEY=vn-xxx or set LLM_API_KEY in .env"
  exit 1
fi

BASE="$BASE_HOST/maas/$USER"
echo "🌐 Base: $BASE"
echo "🔑 Key:  ${KEY:0:12}…${KEY: -6} (length ${#KEY})"
echo ""

# Candidates: full MaaS catalog (user-53461). Lines starting with # are skipped.
# Convention: <provider-lowercase>/<model-slug>
# Tried variants without prefix where slug is unusual.
MODELS=(
  # ── Anthropic Claude ──
  "google/gemma-4-31b-it"
  # "anthropic/claude-sonnet-4-0"
  # "anthropic/claude-3-7-sonnet"
  # "anthropic/claude-3-5-haiku"
  # "anthropic/claude-3-opus"
  # # ── Google Gemini ──
  # "google/gemini-3-1-pro-preview"
  # "google/gemini-2-5-pro"
  # "google/gemini-2-5-flash"
  # "google/gemini-2-5-flash-lite"
  # "google/gemini-2-0-flash"
  # "google/gemini-2-0-flash-lite"
  # # ── Google Gemma ──
  # "google/gemma-3-27b-it"
  # "gemma/gemma-3-27b-it"
  # # ── OpenAI ──
  # "openai/gpt-5"
  # "openai/gpt-5-mini"
  # "openai/gpt-5-nano"
  # "openai/gpt-oss-120b"
  # "openai/gpt-oss-20b"
  # "openai/chatgpt-4o"
  # "openai/gpt-4o"
  # "openai/chatgpt-4o-mini"
  # "openai/gpt-4o-mini"
  # "openai/chatgpt-3-5-turbo"
  # "openai/gpt-3-5-turbo"
  # # ── DeepSeek ──
  # "deepseek/deepseek-reasoner"
  # "deepseek/deepseek-chat"
  # "deepseek/deepseek-r1-qwen3-8b"
  # # ── Qwen ──
  # "qwen/qwen3-5-27b"
  # "qwen/qwen3-235b-a22b-thinking-2507"
  # "qwen/qwen3-235b-a22b-instruct-2507"
  # "qwen/qwen3-vl-235b-a22b-instruct"
  # "qwen/qwen3-30b-a3b-thinking-2507"
  # "qwen/qwen3-coder-plus"
  # "qwen/qwen3-coder-plus-2025-07-22"
  # "qwen/qwen3-coder-480b-a35b-instruct"
  # # ── ByteDance ──
  # "bytedance/bytedance-seed-1-6"
  # "bytedance/bytedance-seed-1-6-flash"
  # "bytedance/skylark-pro"
  # "bytedance/skylark-vision"
  # # ── Meta Llama ──
  # "meta/meta-llama-4-maverick"
  # "meta/meta-llama-4-scout"
  # "meta/meta-llama-3-8b"
  # # ── NVIDIA ──
  # "nvidia/nemotron-3-nano-30b-a3b"
  # # ── GreenNode ──
  # "greennode/greenmind-medium-14b-r1-chat"
  # # ── Embeddings ──
  # "openai/openai-text-embedding-3-large"
  # "openai/openai-text-embedding-3-small"
  # "openai/openai-text-embedding-ada-002"
  # "google/gemini-embedding-001"
  # "qwen/qwen3-embedding-8b"
  # "baai/bge-m3"
  # "greennode/greennode-embedding-large-1007"
  # # ── Speech-to-Text ──
  # "openai/whisper-large-v3"
)

printf "%-40s %-10s %s\n" "MODEL SLUG" "STATUS" "NOTE"
echo "─────────────────────────────────────────────────────────────────────────"

for slug in "${MODELS[@]}"; do
  [[ "$slug" =~ ^# ]] && continue
  url="$BASE/$slug/v1/chat/completions"
  body=$(cat <<EOF
{"model":"$slug","messages":[{"role":"user","content":"hi"}],"max_tokens":3}
EOF
)
  resp=$(curl -s -o /tmp/_probe.json -w "%{http_code}" --max-time 12 \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -X POST "$url" -d "$body")

  case "$resp" in
    200)
      icon="✅"
      note="accessible"
      ;;
    401|403)
      icon="🔒"
      note="auth — wrong key or no permission"
      ;;
    404)
      icon="❌"
      note="not routed — model not on this user/path"
      ;;
    400)
      # 400 often means model exists but request shape wrong (still useful)
      icon="⚠️ "
      body_msg=$(head -c 120 /tmp/_probe.json | tr -d '\n')
      note="400 — model may exist but params wrong: $body_msg"
      ;;
    429)
      icon="⏱ "
      note="rate-limited (model exists)"
      ;;
    5*)
      icon="💥"
      note="server error (model may exist)"
      ;;
    *)
      icon="❓"
      note="unknown response"
      ;;
  esac
  printf "%-40s %-3s %-6s %s\n" "$slug" "$icon" "$resp" "$note"
done

echo ""
echo "Legend: ✅ accessible  ❌ not routed  🔒 no permission  ⚠️  exists but bad request"
echo ""
echo "Tip: add more slugs to the MODELS array if you see a new model on the MaaS portal."
