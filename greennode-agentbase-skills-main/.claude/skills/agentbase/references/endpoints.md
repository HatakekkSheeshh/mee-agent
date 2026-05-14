# GreenNode AgentBase API Endpoints Reference

Centralized reference for all API base URLs used across AgentBase skills. Skills MUST read this file and use ONLY the domains listed below when constructing API URLs.

## DOMAIN VALIDATION — READ THIS FIRST

**ONLY the following API domains exist.** Any other domain is INVALID and must NOT be used:

| Valid Domain | Service |
|---|---|
| `agentbase.api.vngcloud.vn` | AgentBase (Identity, Runtime, Memory) |
| `aiplatform-hcm.api.vngcloud.vn` | AI Platform management (API keys, models) |
| `maas-llm-aiplatform-hcm.api.vngcloud.vn` | LLM inference endpoint (OpenAI-compatible) |
| `vcr.api.vngcloud.vn` | Container Registry |
| `iam.api.vngcloud.vn` | IAM token endpoint |

**NEVER use domains that are NOT in the table above.** In particular:
- `maas.api.vngcloud.vn` — DOES NOT EXIST
- `aiplatform.api.vngcloud.vn` — DOES NOT EXIST (correct: `aiplatform-hcm.api.vngcloud.vn`)
- `agentbase-hcm.api.vngcloud.vn` — DOES NOT EXIST (correct: `agentbase.api.vngcloud.vn`)

Before constructing any curl command, verify the domain matches one of the valid domains above. Do NOT shorten, abbreviate, or modify domain names.

## AgentBase Services

| Service | Base URL | Pagination |
|---------|----------|------------|
| Identity | `https://agentbase.api.vngcloud.vn/identity/api/v1` | 0-indexed (`page=0` is first) |
| Runtime | `https://agentbase.api.vngcloud.vn/runtime` | 1-indexed (`page=1` is first) |
| Memory | `https://agentbase.api.vngcloud.vn/memory` | 1-indexed (`page=1` is first) |

## AI Platform (AIP)

| Service | Base URL | Pagination |
|---------|----------|------------|
| Management API | `https://aiplatform-hcm.api.vngcloud.vn` | 1-indexed |
| LLM Endpoint (OpenAI-compatible) | `https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1` | N/A |

## Container Registry (vCR)

| Service | Base URL | Pagination |
|---------|----------|------------|
| vCR API | `https://vcr.api.vngcloud.vn` | 1-indexed |

## IAM

| Service | Base URL |
|---------|----------|
| Token Endpoint | `https://iam.api.vngcloud.vn/accounts-api/v2/auth/token` |

## Console URLs

| Service | Console URL |
|---------|-------------|
| IAM Service Accounts | `https://iam.console.vngcloud.vn/service-accounts` |
| IAM Policies | `https://iam.console.vngcloud.vn/policies` |
| Identity | `https://aiplatform.console.vngcloud.vn/identity` |
| Runtime | `https://aiplatform.console.vngcloud.vn/runtime` |
| Memory | `https://aiplatform.console.vngcloud.vn/memory` |
| AI Platform | `https://aiplatform.console.vngcloud.vn` |

## Response Shape Reference

API responses use **two different pagination formats** depending on the service:

### Identity Service (Spring-style)
```json
{
  "content": [ ... ],
  "totalElements": 42,
  "totalPages": 5,
  "number": 0,
  "size": 10
}
```
- `content` — array of items
- `totalElements` — total item count across all pages
- `totalPages` — total number of pages
- `number` — current page number (0-indexed)
- `size` — page size

### Runtime / Memory / vCR / AIP (GreenNode-style)
```json
{
  "listData": [ ... ],
  "totalItem": 42,
  "totalPage": 5,
  "page": 1,
  "pageSize": 10
}
```
- `listData` — array of items
- `totalItem` — total item count across all pages
- `totalPage` — total number of pages
- `page` — current page number (1-indexed)
- `pageSize` — page size

### Quick Reference

| Need | Identity Service | Runtime/Memory/vCR/AIP |
|------|-----------------|----------------------|
| Get items | `.content` | `.listData` |
| Total count | `.totalElements` | `.totalItem` |
| Total pages | `.totalPages` | `.totalPage` |
| First page param | `page=0` | `page=1` |
