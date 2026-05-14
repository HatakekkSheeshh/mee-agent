# vCR API Reference

Base URL: `https://vcr.api.vngcloud.vn`

All endpoints require `Authorization: Bearer {iam_access_token}` header.

**Important notes:**
- Pagination query parameters are `page` and `size`. Example: `?page=1&size=50`
- Pagination is **1-based** — `page=1` is the first page. Using `page=0` returns 400 "Page or size invalid".
- The `name=` query parameter must always be present in List Images and List Artifacts requests (even as empty string `name=`), otherwise the API returns 500.
- Re-attaching a robot account after detaching it from a repository returns 500 (backend bug). Use `PUT /v1/user/{repoUserId}/permission` to manage repo access instead of detach/attach.

## Table of Contents
1. [Repository APIs](#repository-apis)
2. [Robot Account APIs](#robot-account-apis)
3. [Permission APIs](#permission-apis)
4. [Image APIs](#image-apis)
5. [Artifact APIs](#artifact-apis)
6. [Data Models](#data-models)

---

## Repository APIs

### List Repositories
```
GET /v1/repository
```

Query params:
- `accessLevel` (string, optional) — filter by access level
- `page` (int, optional) — page number
- `size` (int, optional) — page size

Response: `Paging<RepositoryDto>`

### Create Repository
```
POST /v1/repository
Content-Type: application/json
```

Body (`CreateRepoRequest`):
```json
{
  "repoName": "my-repo",        // required, string — MUST be unique (no duplicate repo names allowed)
  "isPublic": false,             // required, boolean (true=public, false=private)
  "quotaLimit": 10               // required, int, unit: GB
}
```

**Important**: `repoName` must be unique across all repositories. The API will reject creation if a repo with the same name already exists. Always list all repos first (`GET /v1/repository`) and filter client-side to check for duplicates before creating.

Response (202): `RepositoryDto`

### Get Repository by ID
```
GET /v1/repository/{repoId}
```

Path params:
- `repoId` (string) — repository UUID

Response: `RepositoryDto`

### Delete Repository
```
DELETE /v1/repository/{repoId}
```

Path params:
- `repoId` (string) — repository UUID

Response (202): `RepositoryDto`

### Update Repository Quota
```
PUT /v1/repository/{repoId}/quotas
Content-Type: application/json
```

Path params:
- `repoId` (string) — repository UUID

Body (`UpdateRepoQuotaRequest`):
```json
{
  "repoId": "repo-uuid",   // required, string
  "quotaLimit": 20          // required, int, unit: GB
}
```

Response: `RepositoryDto`

### Get Repository History
```
GET /v1/repository/{repoId}/history
```

Path params:
- `repoId` (string) — repository UUID

Query params:
- `page` (int, optional)
- `size` (int, optional)

Response: `Paging<RepositoryHistoryDto>`

### List Robot Accounts Attached to Repository
```
GET /v1/repository/{repoId}/user
```

Path params:
- `repoId` (string) — repository UUID

Query params:
- `name` (string, optional)
- `page` (int, optional)
- `size` (int, optional)

Response: `Paging<RobotAccountRepoPermissionDto>`

### Attach Robot Accounts to Repository
```
PUT /v1/repository/{repoId}/attach
Content-Type: application/json
```

Path params:
- `repoId` (string) — repository UUID

Body (`AttachRepoUserRequest`):
```json
{
  "repoId": "repo-uuid",          // required, string
  "repoUserList": [                // required, array
    {
      "repoUserId": "user-uuid",   // required, string
      "policyIdList": ["policy-uuid-1", "policy-uuid-2"]  // required, array of strings
    }
  ]
}
```

### Detach Robot Accounts from Repository
```
PUT /v1/repository/{repoId}/detach
Content-Type: application/json
```

Path params:
- `repoId` (string) — repository UUID

Body (`DetachRepoUserRequest`):
```json
{
  "repoId": "repo-uuid",                    // required, string
  "repoUserUuidList": ["user-uuid-1"]       // required, array of strings
}
```

---

## Robot Account APIs

### List Robot Accounts
```
GET /v1/user
```

Query params:
- `name` (string, optional) — filter by name
- `page` (int, optional)
- `size` (int, optional)

Response: `Paging<RobotAccountDto>`

### Create Robot Account
```
POST /v1/user
Content-Type: application/json
```

Body (`CreateRepoUserRequest`):
```json
{
  "name": "my-user",                    // required, string
  "description": "optional desc",       // optional, string
  "duration": 365,                      // optional, int, days until expiry
  "permissionRequestList": [            // required, array
    {
      "repoId": "repo-uuid",           // required, string
      "policyIdList": ["policy-uuid"]   // required, array of strings
    }
  ]
}
```

Response: `CreateRepoResponse`
```json
{
  "secretKey": "generated-password"
}
```

**Important**: The `secretKey` is the password. It is only returned once at creation time.
To get the username, list users and find the one whose `backendName` ends with the name you provided.

### Update Robot Account Info
```
PUT /v1/user/{repoUserId}
Content-Type: application/json
```

Path params:
- `repoUserId` (string) — user UUID

Body (`UpdateRepoUserRequest`):
```json
{
  "repoUserId": "user-uuid",           // required, string
  "description": "new description",    // optional, string
  "duration": 180                       // optional, int, days
}
```

Response: `InterfaceRobotAccountEntity`

### Delete Robot Account
```
DELETE /v1/user/{repoUserId}
```

Path params:
- `repoUserId` (string) — user UUID

### Enable Robot Account
```
PUT /v1/user/{repoUserId}/enable
```

Path params:
- `repoUserId` (string) — user UUID

Response: `InterfaceRobotAccountEntity`

### Disable Robot Account
```
PUT /v1/user/{repoUserId}/disable
```

Path params:
- `repoUserId` (string) — user UUID

Response: `InterfaceRobotAccountEntity`

### Update Robot Account Permissions
```
PUT /v1/user/{repoUserId}/permission
Content-Type: application/json
```

Path params:
- `repoUserId` (string) — user UUID

Body (`UpdatePermissionRepoUserRequest`):
```json
{
  "repoUserId": "user-uuid",           // required, string
  "permissionRequestList": [            // required, array
    {
      "repoId": "repo-uuid",
      "policyIdList": ["policy-uuid"]
    }
  ]
}
```

Response: `InterfaceRobotAccountEntity`

### Refresh Robot Account Secret Key
```
GET /v1/user/{repoUserId}/refresh
```

Path params:
- `repoUserId` (string) — user UUID

Response: string (the new secret key)

---

## Permission APIs

### List Permissions
```
GET /v1/user/permissions
```

Response: array of `PolicyDto`
```json
[
  { "uuid": "policy-uuid-1", "action": "push" },
  { "uuid": "policy-uuid-2", "action": "pull" }
]
```

These permission UUIDs are used in `policyIdList` when creating users or attaching users to repositories.

---

## Image APIs

### List Images in Repository
```
GET /v1/repository/{repoId}/images
```

Path params:
- `repoId` (string) — repository UUID

Query params:
- `name` (string, **required** — pass empty string `name=` to list all) — filter by image name
- `page` (int, optional)
- `size` (int, optional)

**Important**: The `name` query parameter must always be present (even as empty string), otherwise the API returns 500.

Response: `Paging<ImageDto>`

### Get Image Detail
```
GET /v1/repository/{repoId}/images/detail
```

Path params:
- `repoId` (string) — repository UUID

Query params:
- `imageName` (string, required) — the image name

Response: `ImageDto`

### Delete Image
```
DELETE /v1/repository/{repoId}/images/delete
```

Path params:
- `repoId` (string) — repository UUID

Query params:
- `imageName` (string, required) — the image name

---

## Artifact APIs

### List Artifacts of Image
```
GET /v1/repository/{repoId}/images/artifacts
```

Path params:
- `repoId` (string) — repository UUID

Query params:
- `imageName` (string, required)
- `name` (string, optional) — filter by artifact tag/name
- `page` (int, optional)
- `size` (int, optional)

Response: `Paging<ArtifactDto>`

### Delete Artifact
```
DELETE /v1/repository/{repoId}/images/artifacts/delete
```

Path params:
- `repoId` (string) — repository UUID

Query params:
- `imageName` (string, required)
- `digest` (string, required) — the artifact digest

---

## Data Models

### RepositoryDto
```json
{
  "uuid": "string",
  "name": "string",
  "backendName": "string",
  "accessLevel": "string",
  "imageCount": 0,
  "attachedUser": 0,
  "quotaLimit": 0,
  "quotaUsed": 0,
  "registryUrl": "string",
  "createdAt": "datetime"
}
```

### RobotAccountDto
```json
{
  "uuid": "string",
  "name": "string",
  "backendName": "string",
  "description": "string",
  "disable": false,
  "expiredAt": "datetime",
  "createdAt": "datetime",
  "userId": 0,
  "numberOfRepo": 0,
  "repoPermissionList": [
    {
      "repoId": "string",
      "repoName": "string",
      "backendRepoName": "string",
      "policyDtoList": [
        { "uuid": "string", "action": "string" }
      ]
    }
  ]
}
```

### RobotAccountRepoPermissionDto
```json
{
  "uuid": "string",
  "name": "string",
  "backendName": "string",
  "description": "string",
  "disable": false,
  "expiredAt": "datetime",
  "createdAt": "datetime",
  "userId": 0,
  "numberOfRepo": 0,
  "policyActionList": ["push", "pull"],
  "repoPermissionList": [...]
}
```

### ImageDto
```json
{
  "name": "string",
  "artifactCount": 0,
  "pullCount": 0,
  "updateTime": "datetime"
}
```

### ArtifactDto
```json
{
  "digest": "string",
  "type": "string",
  "size": 0,
  "pushTime": "datetime",
  "pullTime": "datetime",
  "tags": [
    {
      "id": 0,
      "name": "string",
      "pushTime": "datetime",
      "pullTime": "datetime",
      "artifactId": 0,
      "repositoryId": 0,
      "immutable": false
    }
  ]
}
```

### PolicyDto
```json
{
  "uuid": "string",
  "action": "string"
}
```

### Paging Response Format
All paginated responses follow this structure:
```json
{
  "listData": [...],
  "page": 0,
  "pageSize": 10,
  "totalItem": 100,
  "totalPage": 10
}
```
