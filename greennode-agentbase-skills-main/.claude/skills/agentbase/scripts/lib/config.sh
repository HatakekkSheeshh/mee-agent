#!/usr/bin/env bash
# GreenNode AgentBase API Configuration
# Source this file to get base URLs and constants.
# Usage: source "$(dirname "$0")/lib/config.sh"

# --- Base URLs ---
export AGENTBASE_IDENTITY_URL="https://agentbase.api.vngcloud.vn/identity/api/v1"
export AGENTBASE_RUNTIME_URL="https://agentbase.api.vngcloud.vn/runtime"
export AGENTBASE_MEMORY_URL="https://agentbase.api.vngcloud.vn/memory"
export AIP_MANAGEMENT_URL="https://aiplatform-hcm.api.vngcloud.vn"
export AIP_LLM_URL="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"
export VCR_URL="https://vcr.api.vngcloud.vn"
export IAM_TOKEN_URL="https://iam.api.vngcloud.vn/accounts-api/v2/auth/token"

# --- Pagination defaults ---
# Identity service is 0-indexed; Runtime/Memory/vCR/AIP are 1-indexed
export IDENTITY_FIRST_PAGE=0
export DEFAULT_FIRST_PAGE=1
export DEFAULT_PAGE_SIZE=100

# --- Response field names ---
# Identity uses Spring-style: content, totalElements, totalPages
# Others use GreenNode-style: listData, totalItem, totalPage
