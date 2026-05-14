# Container Registry (vCR) — Full Operations Reference

Detailed operational procedures and workflows for managing Docker container repositories on GreenNode's Container Registry service (vCR).

All operations use the vCR script: `bash .claude/skills/agentbase/scripts/vcr.sh`

The script handles authentication (auto token refresh), response redaction (sensitive fields), and error handling automatically.

For request/response schemas and field descriptions, see `vcr-api.md`.

---

## 1. Repository Management (CRUD)

Repositories are the top-level containers that hold Docker images.

| Action | Command |
|--------|---------|
| List repos | `vcr.sh repo list [--page N] [--size N]` |
| Create repo | `vcr.sh repo create --name NAME [--public] [--quota N]` |
| Get repo by ID | `vcr.sh repo get REPO_ID` |
| Delete repo | `vcr.sh repo delete REPO_ID` (**Before deleting**: You MUST delete all images in the repo first — the API will reject repo deletion if any images remain. Also consider exporting or noting the resource configuration, as deletion is irreversible. There is no undo.) |
| Update quota | `vcr.sh repo update-quota REPO_ID --quota N` |
| Repo history | `vcr.sh repo history REPO_ID` |

**Examples:**

```bash
# List all repositories
bash .claude/skills/agentbase/scripts/vcr.sh repo list --page 1 --size 50

# Create a repository (private by default)
bash .claude/skills/agentbase/scripts/vcr.sh repo create --name my-repo --quota 10

# Create a public repository
bash .claude/skills/agentbase/scripts/vcr.sh repo create --name my-repo --public --quota 10

# Get repository details
bash .claude/skills/agentbase/scripts/vcr.sh repo get $REPO_ID

# Delete a repository (MUST delete all images first — see "Repo Deletion Prerequisite" below)
bash .claude/skills/agentbase/scripts/vcr.sh repo delete $REPO_ID

# Update quota
bash .claude/skills/agentbase/scripts/vcr.sh repo update-quota $REPO_ID --quota 20
```

**Repo Deletion Prerequisite — Delete All Images First:**
The vCR API **will not allow** deleting a repository that still contains images. You MUST delete every image in the repo before deleting, otherwise the API returns an error.

Workflow:
1. List all images: `vcr.sh image list REPO_ID`
2. For each image, delete it: `vcr.sh image delete REPO_ID --name IMAGE_NAME`
3. Paginate if `totalPage > 1` — repeat until all images are deleted
4. Only then delete the repo: `vcr.sh repo delete REPO_ID`

```bash
# Step 1: List all images in the repo
bash .claude/skills/agentbase/scripts/vcr.sh image list $REPO_ID

# Step 2: Delete each image (repeat for every image name returned)
bash .claude/skills/agentbase/scripts/vcr.sh image delete $REPO_ID --name $IMAGE_NAME

# Step 3: After all images are deleted, delete the repo
bash .claude/skills/agentbase/scripts/vcr.sh repo delete $REPO_ID
```

**Before creating**, always check if a repo with the same name already exists — `repoName` must be unique (the backend will reject duplicates):
```bash
bash .claude/skills/agentbase/scripts/vcr.sh repo list --page 1 --size 100
```
Search through the `listData` array client-side to find a repo whose `name` matches. If a matching repo is found, inform the user and offer to **reuse the existing repo** instead of creating a new one. Only proceed with creation if no match is found.

When creating a repository, ask the user for:
- **Repository name** (`repoName`) — lowercase, alphanumeric and hyphens. **Must be unique** — no two repos can have the same name.
- **Access level** — use `--public` flag for public repos. Recommend private (omit `--public`) for most use cases.
- **Quota limit** (`--quota`) — in GB; recommend a sensible default like 10 GB and let the user adjust

---

## 2. Robot Accounts

Robot accounts are service accounts for Docker push/pull access, used to authenticate with the registry.

| Action | Command |
|--------|---------|
| List robot accounts | `vcr.sh robot list [--name NAME]` |
| Create robot account | `vcr.sh robot create --name NAME --repo-id RID --policies PID1,PID2 --output-file PATH [--description DESC] [--duration DAYS]` |
| Get robot account | `vcr.sh robot get ROBOT_ID` |
| Update robot account | `vcr.sh robot update ROBOT_ID [--description DESC] [--duration DAYS]` |
| Delete robot account | `vcr.sh robot delete ROBOT_ID` |
| Enable robot account | `vcr.sh robot enable ROBOT_ID` |
| Disable robot account | `vcr.sh robot disable ROBOT_ID` |
| Update permissions | `vcr.sh robot update-permissions ROBOT_ID --repo-id RID --policies PID1,PID2` |
| Refresh secret key | `vcr.sh robot refresh-key ROBOT_ID` |

**Examples:**

```bash
# List robot accounts
bash .claude/skills/agentbase/scripts/vcr.sh robot list

# List available permissions (fetch before creating robot account)
bash .claude/skills/agentbase/scripts/vcr.sh permissions

# Create robot account with permissions for a repo
bash .claude/skills/agentbase/scripts/vcr.sh robot create \
  --name my-account \
  --repo-id $REPO_ID \
  --policies "$PULL_UUID,$PUSH_UUID" \
  --output-file ./robot-creds.json \
  --duration 365
```

---

## 3. Permissions

Before creating a robot account, fetch the available permissions so the user can choose which ones to grant.

```bash
bash .claude/skills/agentbase/scripts/vcr.sh permissions
```

Returns an array of `{uuid, action}` — typical actions are things like `push`, `pull`, etc.
Always present these options to the user and ask them to select which permissions to grant.

---

## 4. Repository-Robot Account Attachment

Robot accounts can be attached to or detached from repositories:

| Action | Command |
|--------|---------|
| List attached robot accounts | `vcr.sh repo robots REPO_ID` |
| Attach robot accounts | `vcr.sh repo attach REPO_ID --robot-id RID --policies PID1,PID2` |
| Detach robot accounts | `vcr.sh repo detach REPO_ID --robot-ids RID1,RID2` |

---

## 5. Image & Artifact Management

| Action | Command |
|--------|---------|
| List images | `vcr.sh image list REPO_ID [--name NAME]` |
| Get image detail | `vcr.sh image get REPO_ID --name IMAGE_NAME` |
| Delete image | `vcr.sh image delete REPO_ID --name IMAGE_NAME` |
| List artifacts | `vcr.sh artifact list REPO_ID --image IMAGE_NAME` |
| Delete artifact | `vcr.sh artifact delete REPO_ID --image IMAGE_NAME --digest DIGEST` |

> **Important**: The `name=` query parameter must always be present in List Images and List Artifacts requests (even as empty string), otherwise the API returns 500. The script handles this automatically. Pagination is 1-based (`page=1` is the first page; `page=0` returns 400).

**Examples:**

```bash
# List images in a repository
bash .claude/skills/agentbase/scripts/vcr.sh image list $REPO_ID

# List images filtered by name
bash .claude/skills/agentbase/scripts/vcr.sh image list $REPO_ID --name my-image

# Get image details
bash .claude/skills/agentbase/scripts/vcr.sh image get $REPO_ID --name my-image

# List artifacts for a specific image
bash .claude/skills/agentbase/scripts/vcr.sh artifact list $REPO_ID --image my-image

# Delete an artifact
bash .claude/skills/agentbase/scripts/vcr.sh artifact delete $REPO_ID --image my-image --digest $DIGEST
```

---

## Key Workflow: Create a Robot Account for a Repository

This is the most common end-to-end workflow. Follow these steps in order:

### Step 1: Identify the repository
- If the user specifies a repo name or ID, use it directly.
- If not, list repositories and help the user pick one:
  ```bash
  bash .claude/skills/agentbase/scripts/vcr.sh repo list
  ```
- If no repository exists yet, offer to create one first. **Before creating**, always list all repos and filter client-side for existing repos with the same name (see Section 1 above) — `repoName` must be unique.
- Note: the repository's `backendName` field is the actual repo name used in the Docker image path (`vcr.vngcloud.vn/{backendName}/{imageName}:{tag}`).

### Step 2: Fetch and present permissions
- Fetch the list of available permissions:
  ```bash
  bash .claude/skills/agentbase/scripts/vcr.sh permissions
  ```
- Present them to the user in a readable format (e.g., a numbered list showing each action).
- **Recommend `pull` + `push`** as the default — this covers most use cases and avoids issues with the "all" permission option (see Known API Quirks in SKILL.md).
- Ask the user which permissions they want to grant, but default to pull + push if they don't have a preference.

### Step 3: Create the robot account
- Ask for an account name (and optionally description, duration in days).
- Create the robot account:
  ```bash
  bash .claude/skills/agentbase/scripts/vcr.sh robot create \
    --name chosen-name \
    --repo-id $REPO_ID \
    --policies "$PULL_UUID,$PUSH_UUID" \
    --output-file <credentials-file-path> \
    --description "optional description" \
    --duration 365
  ```
- The script automatically handles response redaction — the **secretKey** is shown as `********` in the output.
- **Auto-save**: The `robot create` command saves credentials to the file specified by `--output-file <path>`. The `backendName` from the response is used as the Docker username. No manual credential extraction is needed.
- The raw response is saved to `.agentbase/vcr_robot_create_response.json` (a dedicated file that won't be overwritten by subsequent API calls).
- **IMPORTANT**: The secretKey cannot be retrieved again from the API. If you need to regenerate it later, use `robot refresh-key`.

### Step 4: Verify credentials and login Docker
- Verify credentials were saved:
  ```bash
  bash .claude/skills/agentbase/scripts/check_credentials.sh registry --credentials-file <path>
  ```
- If the `--repository` field is needed (for image path reference), ask the user for the repository path and save it using `save_registry_credentials.sh` with `--output-file <path>` and `--repository`.
- Login to Docker:
  ```bash
  bash .claude/skills/agentbase/scripts/docker_login.sh
  ```
- Tell the user how to use the credentials (use the repo's `backendName` as `{repoBackendName}`):
  ```bash
  # Tag and push an image
  docker tag myimage:latest vcr.vngcloud.vn/{repoBackendName}/{imageName}:{tag}
  docker push vcr.vngcloud.vn/{repoBackendName}/{imageName}:{tag}

  # Pull an image
  docker pull vcr.vngcloud.vn/{repoBackendName}/{imageName}:{tag}
  ```

> **WARNING**: Do NOT call `robot list` or any other API between `robot create` and credential verification. Each API call overwrites `.agentbase/last_response.json`. The `robot create` command saves to a dedicated file (`.agentbase/vcr_robot_create_response.json`) to prevent this issue, but avoid relying on `last_response.json` for secretKey retrieval.
