#!/usr/bin/env bash
# GreenNode vCR (Container Registry) Management
# Usage: bash .claude/skills/agentbase/scripts/vcr.sh <resource> <action> [options]

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPTS_DIR/lib/config.sh"
source "$SCRIPTS_DIR/lib/common.sh"

BASE_URL="$VCR_URL"

# --- Parse resource + action + common flags ---
RESOURCE="${1:-help}"; shift 2>/dev/null || true
ACTION="${1:-help}"; shift 2>/dev/null || true
ARGS=()
while IFS= read -r line; do ARGS+=("$line"); done < <(parse_flags "$@")
if [ ${#ARGS[@]} -gt 0 ]; then set -- "${ARGS[@]}"; else set --; fi

# ===========================
# Repository actions
# ===========================

repo_list() {
  local page="$DEFAULT_FIRST_PAGE" size="$DEFAULT_PAGE_SIZE" access_level="ALL"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --access-level) access_level="$2"; shift 2 ;;
      --page) page="$2"; shift 2 ;;
      --size) size="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for repo list: $1" >&2; return 1 ;;
    esac
  done

  local query
  query=$(build_query "accessLevel=$access_level" "page=$page" "size=$size")
  api_call GET "${BASE_URL}/v1/repository${query}"
}

repo_create() {
  local name="" is_public=false quota=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --public) is_public=true; shift ;;
      --quota) quota="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for repo create: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$name" ]; then
    echo "ERROR: --name is required for repo create" >&2
    return 1
  fi
  if [ -z "$quota" ]; then
    echo "ERROR: --quota is required for repo create (integer, GB)" >&2
    return 1
  fi

  local body
  body=$(jq -n \
    --arg repoName "$name" \
    --argjson isPublic "$is_public" \
    --argjson quotaLimit "$quota" \
    '{repoName: $repoName, isPublic: $isPublic, quotaLimit: $quotaLimit}')

  api_call POST "${BASE_URL}/v1/repository" "$body"
}

repo_get() {
  local repo_id="${1:-}"
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for repo get" >&2
    return 1
  fi
  api_call GET "${BASE_URL}/v1/repository/${repo_id}"
}

repo_delete() {
  local repo_id="${1:-}"
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for repo delete" >&2
    return 1
  fi
  api_call DELETE "${BASE_URL}/v1/repository/${repo_id}"
}

repo_update_quota() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for repo update-quota" >&2
    return 1
  fi

  local quota=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --quota) quota="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for repo update-quota: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$quota" ]; then
    echo "ERROR: --quota is required for repo update-quota" >&2
    return 1
  fi

  local body
  body=$(jq -n \
    --arg repoId "$repo_id" \
    --argjson quotaLimit "$quota" \
    '{repoId: $repoId, quotaLimit: $quotaLimit}')

  api_call PUT "${BASE_URL}/v1/repository/${repo_id}/quotas" "$body"
}

repo_history() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for repo history" >&2
    return 1
  fi

  local page="$DEFAULT_FIRST_PAGE" size="$DEFAULT_PAGE_SIZE"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --page) page="$2"; shift 2 ;;
      --size) size="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for repo history: $1" >&2; return 1 ;;
    esac
  done

  local query
  query=$(build_query "page=$page" "size=$size")
  api_call GET "${BASE_URL}/v1/repository/${repo_id}/history${query}"
}

repo_robots() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for repo robots" >&2
    return 1
  fi

  local name="" page="$DEFAULT_FIRST_PAGE" size="$DEFAULT_PAGE_SIZE"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --page) page="$2"; shift 2 ;;
      --size) size="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for repo robots: $1" >&2; return 1 ;;
    esac
  done

  local query
  query=$(build_query "name=$name" "page=$page" "size=$size")
  api_call GET "${BASE_URL}/v1/repository/${repo_id}/user${query}"
}

repo_attach() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for repo attach" >&2
    return 1
  fi

  local robot_id="" policies=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --robot-id) robot_id="$2"; shift 2 ;;
      --policies) policies="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for repo attach: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$robot_id" ] || [ -z "$policies" ]; then
    echo "ERROR: --robot-id and --policies are required for repo attach" >&2
    return 1
  fi

  local policy_list
  policy_list=$(echo "$policies" | tr ',' '\n' | jq -R . | jq -s .)

  local body
  body=$(jq -n \
    --arg repoId "$repo_id" \
    --arg repoUserId "$robot_id" \
    --argjson policyIdList "$policy_list" \
    '{repoId: $repoId, repoUserList: [{repoUserId: $repoUserId, policyIdList: $policyIdList}]}')

  api_call PUT "${BASE_URL}/v1/repository/${repo_id}/attach" "$body"
}

repo_detach() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for repo detach" >&2
    return 1
  fi

  local robot_ids=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --robot-ids) robot_ids="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for repo detach: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$robot_ids" ]; then
    echo "ERROR: --robot-ids is required for repo detach" >&2
    return 1
  fi

  local uuid_list
  uuid_list=$(echo "$robot_ids" | tr ',' '\n' | jq -R . | jq -s .)

  local body
  body=$(jq -n \
    --arg repoId "$repo_id" \
    --argjson repoUserUuidList "$uuid_list" \
    '{repoId: $repoId, repoUserUuidList: $repoUserUuidList}')

  api_call PUT "${BASE_URL}/v1/repository/${repo_id}/detach" "$body"
}

# ===========================
# Robot Account actions
# ===========================

robot_list() {
  local name="" page="$DEFAULT_FIRST_PAGE" size="$DEFAULT_PAGE_SIZE"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --page) page="$2"; shift 2 ;;
      --size) size="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for robot list: $1" >&2; return 1 ;;
    esac
  done

  local query
  query=$(build_query "name=$name" "page=$page" "size=$size")
  api_call GET "${BASE_URL}/v1/user${query}"
}

robot_create() {
  local name="" repo_id="" policies="" description="" duration="" output=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --repo-id) repo_id="$2"; shift 2 ;;
      --policies) policies="$2"; shift 2 ;;
      --description) description="$2"; shift 2 ;;
      --duration) duration="$2"; shift 2 ;;
      --output-file|-o) output="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for robot create: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$name" ]; then
    echo "ERROR: --name is required for robot create" >&2
    return 1
  fi
  if [ -z "$repo_id" ] || [ -z "$policies" ]; then
    echo "ERROR: --repo-id and --policies are required for robot create" >&2
    return 1
  fi
  if [ -z "$output" ]; then
    echo "ERROR: --output-file <path> is required for robot create (where to save credentials)" >&2
    return 1
  fi

  local policy_list
  policy_list=$(echo "$policies" | tr ',' '\n' | jq -R . | jq -s .)

  local body
  body=$(jq -n \
    --arg name "$name" \
    --arg description "$description" \
    --arg duration "$duration" \
    --arg repoId "$repo_id" \
    --argjson policyIdList "$policy_list" \
    '{name: $name} +
     (if $description != "" then {description: $description} else {} end) +
     (if $duration != "" then {duration: ($duration | tonumber)} else {} end) +
     {permissionRequestList: [{repoId: $repoId, policyIdList: $policyIdList}]}')

  SAVE_AS="$AGENTBASE_DIR/vcr_robot_create_response.json" REDACT_FIELDS="secretKey" \
    api_call POST "${BASE_URL}/v1/user" "$body"

  # Save credentials using the dedicated response file (immune to overwrites by subsequent API calls)
  local raw_response
  raw_response=$(cat "$AGENTBASE_DIR/vcr_robot_create_response.json" 2>/dev/null || true)
  local secret_key robot_name
  secret_key=$(echo "$raw_response" | jq -r '.secretKey // empty' 2>/dev/null || true)
  # Use backendName (e.g. "109072-my-account") as Docker username; fall back to name
  robot_name=$(echo "$raw_response" | jq -r '.backendName // .name // empty' 2>/dev/null || true)

  # If API response lacks backendName (common: create returns only secretKey),
  # look up the robot we just created to get the backendName for Docker login
  if [ -n "$secret_key" ] && [ -z "$robot_name" ] && [ -n "$name" ]; then
    local lookup_response
    lookup_response=$(SAVE_AS="" REDACT_FIELDS="" api_call GET "${BASE_URL}/v1/user?name=${name}&page=$DEFAULT_FIRST_PAGE&size=$DEFAULT_PAGE_SIZE" 2>/dev/null || true)
    robot_name=$(echo "$lookup_response" | jq -r --arg n "$name" \
      '.listData[]? | select(.name == $n) | .backendName // empty' 2>/dev/null | head -1 || true)
  fi

  if [ -n "$secret_key" ] && [ -n "$robot_name" ]; then
    echo "$secret_key" | bash "$SCRIPTS_DIR/save_registry_credentials.sh" \
      --output-file "$output" \
      --username "$robot_name" \
      --password-stdin \
      --registry "vcr.vngcloud.vn"
  fi
}

robot_get() {
  local robot_id="${1:-}"
  if [ -z "$robot_id" ]; then
    echo "ERROR: ROBOT_ID argument is required for robot get" >&2
    return 1
  fi
  api_call GET "${BASE_URL}/v1/user/${robot_id}"
}

robot_delete() {
  local robot_id="${1:-}"
  if [ -z "$robot_id" ]; then
    echo "ERROR: ROBOT_ID argument is required for robot delete" >&2
    return 1
  fi
  api_call DELETE "${BASE_URL}/v1/user/${robot_id}"
}

robot_enable() {
  local robot_id="${1:-}"
  if [ -z "$robot_id" ]; then
    echo "ERROR: ROBOT_ID argument is required for robot enable" >&2
    return 1
  fi
  api_call PUT "${BASE_URL}/v1/user/${robot_id}/enable"
}

robot_disable() {
  local robot_id="${1:-}"
  if [ -z "$robot_id" ]; then
    echo "ERROR: ROBOT_ID argument is required for robot disable" >&2
    return 1
  fi
  api_call PUT "${BASE_URL}/v1/user/${robot_id}/disable"
}

robot_update_permissions() {
  local robot_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$robot_id" ]; then
    echo "ERROR: ROBOT_ID argument is required for robot update-permissions" >&2
    return 1
  fi

  local repo_id="" policies=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-id) repo_id="$2"; shift 2 ;;
      --policies) policies="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for robot update-permissions: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$repo_id" ] || [ -z "$policies" ]; then
    echo "ERROR: --repo-id and --policies are required for robot update-permissions" >&2
    return 1
  fi

  local policy_list
  policy_list=$(echo "$policies" | tr ',' '\n' | jq -R . | jq -s .)

  local body
  body=$(jq -n \
    --arg repoUserId "$robot_id" \
    --arg repoId "$repo_id" \
    --argjson policyIdList "$policy_list" \
    '{repoUserId: $repoUserId, permissionRequestList: [{repoId: $repoId, policyIdList: $policyIdList}]}')

  api_call PUT "${BASE_URL}/v1/user/${robot_id}/permission" "$body"
}

robot_refresh_key() {
  local robot_id="${1:-}"
  if [ -z "$robot_id" ]; then
    echo "ERROR: ROBOT_ID argument is required for robot refresh-key" >&2
    return 1
  fi
  SAVE_AS="$AGENTBASE_DIR/vcr_robot_refresh_response.json" REDACT_FIELDS="secretKey" \
    api_call GET "${BASE_URL}/v1/user/${robot_id}/refresh"

  # Update saved credentials with the new key (dedicated file, immune to overwrites)
  local raw_response
  raw_response=$(cat "$AGENTBASE_DIR/vcr_robot_refresh_response.json" 2>/dev/null || true)

  local secret_key robot_name
  # Handle both JSON response (with secretKey field) and raw text response (just the key)
  if echo "$raw_response" | jq -e '.secretKey' &>/dev/null; then
    secret_key=$(echo "$raw_response" | jq -r '.secretKey // empty')
    robot_name=$(echo "$raw_response" | jq -r '.backendName // .name // empty')
  else
    # API may return the raw secret key as plain text
    secret_key=$(echo "$raw_response" | tr -d '[:space:]')
    robot_name=""
  fi

  if [ -n "$secret_key" ] && [ -n "$robot_name" ]; then
    echo "NOTE: New secret key generated for robot '$robot_name'. Save it using save_registry_credentials.sh --output-file <path>"
  fi
}

robot_update() {
  local robot_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$robot_id" ]; then
    echo "ERROR: ROBOT_ID argument is required for robot update" >&2
    return 1
  fi

  local description="" duration=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --description) description="$2"; shift 2 ;;
      --duration) duration="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for robot update: $1" >&2; return 1 ;;
    esac
  done

  local body
  body=$(jq -n \
    --arg repoUserId "$robot_id" \
    --arg description "$description" \
    --arg duration "$duration" \
    '{repoUserId: $repoUserId} +
     (if $description != "" then {description: $description} else {} end) +
     (if $duration != "" then {duration: ($duration | tonumber)} else {} end)')

  api_call PUT "${BASE_URL}/v1/user/${robot_id}" "$body"
}

# ===========================
# Permission actions
# ===========================

do_permissions() {
  api_call GET "${BASE_URL}/v1/user/permissions"
}

# ===========================
# Image actions
# ===========================

image_list() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for image list" >&2
    return 1
  fi

  local name="" page="$DEFAULT_FIRST_PAGE" size="$DEFAULT_PAGE_SIZE"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --page) page="$2"; shift 2 ;;
      --size) size="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for image list: $1" >&2; return 1 ;;
    esac
  done

  # name query param is required by the API (even if empty)
  local query
  query=$(build_query "page=$page" "size=$size")
  # Always include name= param
  if [ -n "$query" ]; then
    query="${query}&name=${name}"
  else
    query="?name=${name}"
  fi
  api_call GET "${BASE_URL}/v1/repository/${repo_id}/images${query}"
}

image_get() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for image get" >&2
    return 1
  fi

  local name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for image get: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$name" ]; then
    echo "ERROR: --name is required for image get" >&2
    return 1
  fi

  api_call GET "${BASE_URL}/v1/repository/${repo_id}/images/detail?imageName=${name}"
}

image_delete() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for image delete" >&2
    return 1
  fi

  local name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for image delete: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$name" ]; then
    echo "ERROR: --name is required for image delete" >&2
    return 1
  fi

  api_call DELETE "${BASE_URL}/v1/repository/${repo_id}/images/delete?imageName=${name}"
}

# ===========================
# Artifact actions
# ===========================

artifact_list() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for artifact list" >&2
    return 1
  fi

  local image="" name="" page="$DEFAULT_FIRST_PAGE" size="$DEFAULT_PAGE_SIZE"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image) image="$2"; shift 2 ;;
      --name) name="$2"; shift 2 ;;
      --page) page="$2"; shift 2 ;;
      --size) size="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for artifact list: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$image" ]; then
    echo "ERROR: --image is required for artifact list" >&2
    return 1
  fi

  local query
  query=$(build_query "imageName=$image" "name=$name" "page=$page" "size=$size")
  api_call GET "${BASE_URL}/v1/repository/${repo_id}/images/artifacts${query}"
}

artifact_delete() {
  local repo_id="${1:-}"; shift 2>/dev/null || true
  if [ -z "$repo_id" ]; then
    echo "ERROR: REPO_ID argument is required for artifact delete" >&2
    return 1
  fi

  local image="" digest=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image) image="$2"; shift 2 ;;
      --digest) digest="$2"; shift 2 ;;
      *) echo "ERROR: Unknown option for artifact delete: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$image" ] || [ -z "$digest" ]; then
    echo "ERROR: --image and --digest are required for artifact delete" >&2
    return 1
  fi

  api_call DELETE "${BASE_URL}/v1/repository/${repo_id}/images/artifacts/delete?imageName=${image}&digest=${digest}"
}

# ===========================
# Help
# ===========================

do_help() {
  show_help ".claude/skills/agentbase/scripts/vcr.sh" \
    "Manage GreenNode vCR (Container Registry) resources." \
    "  repo list       [--page N] [--size N]                            List repositories
  repo create     --name NAME [--public] [--quota N]                Create a repository
  repo get        REPO_ID                                           Get repository details
  repo delete     REPO_ID                                           Delete a repository
  repo update-quota REPO_ID --quota N                               Update repository quota
  repo history    REPO_ID [--page N] [--size N]                     Show repository history
  repo robots     REPO_ID [--page N] [--size N]                     List attached robots
  repo attach     REPO_ID --robot-id RID --policies PID1,PID2      Attach robot to repo
  repo detach     REPO_ID --robot-ids RID1,RID2                     Detach robots from repo

  robot list      [--name NAME] [--page N] [--size N]               List robot accounts
  robot create    --name NAME --repo-id RID --policies P1,P2        Create robot account
                  [--description DESC] [--duration DAYS]
  robot get       ROBOT_ID                                          Get robot account
  robot update    ROBOT_ID [--description DESC] [--duration DAYS]   Update robot info
  robot delete    ROBOT_ID                                          Delete robot account
  robot enable    ROBOT_ID                                          Enable robot account
  robot disable   ROBOT_ID                                          Disable robot account
  robot update-permissions ROBOT_ID --repo-id RID --policies P1,P2  Update permissions
  robot refresh-key ROBOT_ID                                        Refresh secret key

  permissions                                                       List available permissions

  image list      REPO_ID [--name NAME] [--page N] [--size N]       List images
  image get       REPO_ID --name NAME                               Get image details
  image delete    REPO_ID --name NAME                               Delete an image

  artifact list   REPO_ID --image NAME [--name NAME] [--page N]     List artifacts
  artifact delete REPO_ID --image NAME --digest DIGEST              Delete an artifact

  help                                                              Show this help message"
}

# ===========================
# Dispatch
# ===========================

case "$RESOURCE" in
  repo)
    case "$ACTION" in
      list)           repo_list "$@" ;;
      create)         repo_create "$@" ;;
      get)            repo_get "$@" ;;
      delete)         repo_delete "$@" ;;
      update-quota)   repo_update_quota "$@" ;;
      history)        repo_history "$@" ;;
      robots)         repo_robots "$@" ;;
      attach)         repo_attach "$@" ;;
      detach)         repo_detach "$@" ;;
      help)           do_help ;;
      *)              echo "ERROR: Unknown repo action '$ACTION'. Run with 'help' for usage." >&2; exit 1 ;;
    esac
    ;;
  robot)
    case "$ACTION" in
      list)               robot_list "$@" ;;
      create)             robot_create "$@" ;;
      get)                robot_get "$@" ;;
      update)             robot_update "$@" ;;
      delete)             robot_delete "$@" ;;
      enable)             robot_enable "$@" ;;
      disable)            robot_disable "$@" ;;
      update-permissions) robot_update_permissions "$@" ;;
      refresh-key)        robot_refresh_key "$@" ;;
      help)               do_help ;;
      *)                  echo "ERROR: Unknown robot action '$ACTION'. Run with 'help' for usage." >&2; exit 1 ;;
    esac
    ;;
  permissions)
    do_permissions ;;
  image)
    case "$ACTION" in
      list)   image_list "$@" ;;
      get)    image_get "$@" ;;
      delete) image_delete "$@" ;;
      help)   do_help ;;
      *)      echo "ERROR: Unknown image action '$ACTION'. Run with 'help' for usage." >&2; exit 1 ;;
    esac
    ;;
  artifact)
    case "$ACTION" in
      list)   artifact_list "$@" ;;
      delete) artifact_delete "$@" ;;
      help)   do_help ;;
      *)      echo "ERROR: Unknown artifact action '$ACTION'. Run with 'help' for usage." >&2; exit 1 ;;
    esac
    ;;
  help) do_help ;;
  *)    echo "ERROR: Unknown resource '$RESOURCE'. Run with 'help' for usage." >&2; exit 1 ;;
esac
