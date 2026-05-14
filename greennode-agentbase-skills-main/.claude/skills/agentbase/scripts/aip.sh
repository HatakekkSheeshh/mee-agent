#!/usr/bin/env bash
# GreenNode AI Platform — API Key & LLM Model Management
# Usage: bash .claude/skills/agentbase/scripts/aip.sh <resource> <action> [options]

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPTS_DIR/lib/config.sh"
source "$SCRIPTS_DIR/lib/common.sh"

API_KEYS_URL="$AIP_MANAGEMENT_URL/v1/api-keys"
MODELS_URL="$AIP_MANAGEMENT_URL/v1/models"
MODELS_V2_URL="$AIP_MANAGEMENT_URL/v2/models"

# --- Parse resource + action + common flags ---
RESOURCE="${1:-help}"; shift 2>/dev/null || true
ACTION="${1:-help}"; shift 2>/dev/null || true
ARGS=()
while IFS= read -r line; do ARGS+=("$line"); done < <(parse_flags "$@")
if [ ${#ARGS[@]} -gt 0 ]; then set -- "${ARGS[@]}"; else set --; fi

# =====================================================================
# API Keys
# =====================================================================

apikeys_list() {
  local name="" page="$DEFAULT_FIRST_PAGE" size="$DEFAULT_PAGE_SIZE"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --page) page="$2"; shift 2 ;;
      --size) size="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for api-keys list: $1" >&2; return 1 ;;
    esac
  done

  local query
  query=$(build_query "name=$name" "page=$page" "size=$size")
  REDACT_FIELDS="key" api_call GET "${API_KEYS_URL}${query}"
}

apikeys_create() {
  local name="" is_default="false"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --default) is_default="true"; shift ;;
      *) echo "ERROR: Unknown option for api-keys create: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$name" ]; then
    echo "ERROR: --name is required for api-keys create" >&2
    return 1
  fi

  # Validate name format
  if ! [[ "$name" =~ ^[a-z0-9\-]{5,50}$ ]]; then
    echo "ERROR: Name must match ^[a-z0-9-]{5,50}$ (lowercase alphanumeric and hyphens, 5-50 chars)" >&2
    return 1
  fi

  local body
  body=$(jq -n \
    --arg name "$name" \
    --argjson isDefault "$is_default" \
    '{name: $name, isDefault: $isDefault}')

  # Save full response to dedicated file (immune to overwrites by subsequent calls)
  # Redact key from stdout so it doesn't leak into LLM context
  SAVE_AS="$AGENTBASE_DIR/aip_apikey_create_response.json" REDACT_FIELDS="key" \
    api_call POST "$API_KEYS_URL" "$body" || return 1

  # Extract key from saved response and write to .env via save_env_var.sh
  # (key never appears on command line — piped via stdin)
  local raw_key=""
  raw_key=$(jq -r '.data.key // .key // empty' "$AGENTBASE_DIR/aip_apikey_create_response.json" 2>/dev/null)

  if [ -n "$raw_key" ]; then
    echo "$raw_key" | bash "$(dirname "$0")/save_env_var.sh" --key LLM_API_KEY --value-stdin >&2
  else
    echo "WARNING: Could not extract API key from response. Retrieve it manually with: api-keys get $name" >&2
  fi

  echo "Key created. Poll status with: api-keys get $name" >&2
}

apikeys_get() {
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo "ERROR: Name argument is required for api-keys get" >&2
    return 1
  fi
  REDACT_FIELDS="key" api_call GET "${API_KEYS_URL}/${name}"
}

apikeys_update() {
  local name="${1:-}"; shift 2>/dev/null || true
  if [ -z "$name" ]; then
    echo "ERROR: Name argument is required for api-keys update" >&2
    return 1
  fi

  local is_default=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --default) is_default="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for api-keys update: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$is_default" ]; then
    echo "ERROR: --default true|false is required for api-keys update" >&2
    return 1
  fi

  if [[ "$is_default" != "true" && "$is_default" != "false" ]]; then
    echo "ERROR: --default value must be 'true' or 'false', got: '$is_default'" >&2
    return 1
  fi

  local body
  body=$(jq -n --argjson isDefault "$is_default" '{isDefault: $isDefault}')
  REDACT_FIELDS="key" api_call PUT "${API_KEYS_URL}/${name}" "$body"
}

apikeys_delete() {
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo "ERROR: Name argument is required for api-keys delete" >&2
    return 1
  fi

  REDACT_FIELDS="key" api_call DELETE "${API_KEYS_URL}/${name}" || return 1
  echo "Delete request sent. Poll status with: api-keys get $name (expect 404 when done)" >&2
}

# =====================================================================
# Models
# =====================================================================

models_list() {
  local name="" status="" page="$DEFAULT_FIRST_PAGE" size="$DEFAULT_PAGE_SIZE"
  local providers="" types="" use_cases="" resource_type="" zone=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --providers) providers="$2"; shift 2 ;;
      --types) types="$2"; shift 2 ;;
      --use-cases) use_cases="$2"; shift 2 ;;
      --status) status="$2"; shift 2 ;;
      --resource-type) resource_type="$2"; shift 2 ;;
      --zone) zone="$2"; shift 2 ;;
      --page) page="$2"; shift 2 ;;
      --size) size="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for models list: $1" >&2; return 1 ;;
    esac
  done

  # Build base query params
  local query
  query=$(build_query "name=$name" "status=$status" "resourceType=$resource_type" "zone=$zone" "page=$page" "size=$size")

  # Convert comma-separated array params to repeated query params
  local array_params=""
  if [ -n "$providers" ]; then
    IFS=',' read -ra items <<< "$providers"
    for item in "${items[@]}"; do
      array_params+="&providers=$(echo "$item" | xargs)"
    done
  fi
  if [ -n "$types" ]; then
    IFS=',' read -ra items <<< "$types"
    for item in "${items[@]}"; do
      array_params+="&modelTypes=$(echo "$item" | xargs)"
    done
  fi
  if [ -n "$use_cases" ]; then
    IFS=',' read -ra items <<< "$use_cases"
    for item in "${items[@]}"; do
      array_params+="&useCases=$(echo "$item" | xargs)"
    done
  fi

  # Append array params
  if [ -n "$array_params" ]; then
    if [ -n "$query" ]; then
      query+="$array_params"
    else
      query="?${array_params:1}"  # strip leading &
    fi
  fi

  # Capture raw response, then strip bulky image fields for clean output
  local raw_output
  raw_output=$(api_call GET "${MODELS_URL}${query}") || return 1
  echo "$raw_output" | jq 'if .listData then .listData |= map(del(.image)) else . end' 2>/dev/null || echo "$raw_output"
}

models_get() {
  local model_uuid="${1:-}"
  if [ -z "$model_uuid" ]; then
    echo "ERROR: MODEL_UUID argument is required for models get" >&2
    return 1
  fi
  local raw_output
  raw_output=$(api_call GET "${MODELS_URL}/detail/${model_uuid}") || return 1
  echo "$raw_output" | jq 'del(.image)' 2>/dev/null || echo "$raw_output"
}

models_metadata() {
  api_call GET "${MODELS_URL}/metadata"
}

models_enable() {
  local model_uuid="${1:-}"
  if [ -z "$model_uuid" ]; then
    echo "ERROR: MODEL_UUID argument is required for models enable" >&2
    return 1
  fi
  local body
  body=$(jq -n --arg uuid "$model_uuid" '[{modelUuid: $uuid, enabled: true}]')
  api_call POST "${MODELS_V2_URL}/user-settings" "$body"
}

models_disable() {
  local model_uuid="${1:-}"
  if [ -z "$model_uuid" ]; then
    echo "ERROR: MODEL_UUID argument is required for models disable" >&2
    return 1
  fi
  local body
  body=$(jq -n --arg uuid "$model_uuid" '[{modelUuid: $uuid, enabled: false}]')
  api_call POST "${MODELS_V2_URL}/user-settings" "$body"
}

models_rate_limit() {
  local model_uuid="${1:-}"
  if [ -z "$model_uuid" ]; then
    echo "ERROR: MODEL_UUID argument is required for models rate-limit" >&2
    return 1
  fi
  api_call GET "${MODELS_URL}/rate-limit/${model_uuid}"
}

# =====================================================================
# Help
# =====================================================================

do_help() {
  show_help ".claude/skills/agentbase/scripts/aip.sh" \
    "Manage GreenNode AI Platform API keys and LLM models." \
    "  api-keys list   [--name NAME] [--page N] [--size N]   List API keys
  api-keys create --name NAME [--default]                Create a new API key
  api-keys get    NAME                                   Get API key by name
  api-keys update NAME [--default true|false]            Update API key
  api-keys delete NAME                                   Delete API key
  models   list   [--name N] [--providers P1,P2] [--types T1,T2] [--status S] [--page N] [--size N]
                                                         List models
  models   get    MODEL_UUID                             Get model detail
  models   metadata                                      Get filter metadata
  models   enable  MODEL_UUID                            Enable a model
  models   disable MODEL_UUID                            Disable a model
  models   rate-limit MODEL_UUID                         Get model rate limits
  help                                                   Show this help message"
}

# --- Dispatch ---
case "$RESOURCE" in
  api-keys)
    case "$ACTION" in
      list)   apikeys_list "$@" ;;
      create) apikeys_create "$@" ;;
      get)    apikeys_get "$@" ;;
      update) apikeys_update "$@" ;;
      delete) apikeys_delete "$@" ;;
      help)   do_help ;;
      *)      echo "ERROR: Unknown api-keys action '$ACTION'. Run with 'help' for usage." >&2; exit 1 ;;
    esac
    ;;
  models)
    case "$ACTION" in
      list)       models_list "$@" ;;
      get)        models_get "$@" ;;
      metadata)   models_metadata ;;
      enable)     models_enable "$@" ;;
      disable)    models_disable "$@" ;;
      rate-limit) models_rate_limit "$@" ;;
      help)       do_help ;;
      *)          echo "ERROR: Unknown models action '$ACTION'. Run with 'help' for usage." >&2; exit 1 ;;
    esac
    ;;
  help) do_help ;;
  *)    echo "ERROR: Unknown resource '$RESOURCE'. Run with 'help' for usage." >&2; exit 1 ;;
esac
