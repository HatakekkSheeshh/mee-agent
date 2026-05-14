# GreenNode AgentBase Skills - User Guide

Setup and usage guide for GreenNode AgentBase skills across **Claude Code**, **Cursor**, and **OpenAI Codex**.

---

## Quick Start

```bash
# Copy skills into your project
cp -r <skills-repo>/.claude/skills/ <your-project>/.claude/skills/

# Launch Claude Code
cd <your-project>
claude

# Then use slash commands or natural language
/agentbase-wizard                    # Guided wizard from A to Z
/agentbase-wizard init my-agent      # Scaffold a new project
/agentbase-deploy                    # Deploy agent
"Create a new agent with LangGraph"  # Claude picks the right skill
```

---

## Setup by Tool

All skills follow the [SKILL.md standard](https://www.mintlify.com/blog/skill-md). Copy them into the correct directory and they're auto-detected.

### Claude Code

```bash
# Project-level
cp -r <skills-repo>/.claude/skills/ <your-project>/.claude/skills/

# Or global (all projects)
cp -r <skills-repo>/.claude/skills/* ~/.claude/skills/
```

```bash
cd <your-project> && claude
```

Use `/skill-name` or natural language — Claude auto-picks the right skill.

### Cursor (v2.4+)

```bash
# Project-level
cp -r <skills-repo>/.claude/skills/ <your-project>/.cursor/skills/

# Or global
cp -r <skills-repo>/.claude/skills/* ~/.cursor/skills/
```

```bash
cd <your-project> && cursor .
```

Type `/` in Agent chat to search skills. For deploy & monitor, open terminal and run `claude`.

### OpenAI Codex

```bash
# Project-level
cp -r <skills-repo>/.claude/skills/ <your-project>/.agents/skills/

# Or global
cp -r <skills-repo>/.claude/skills/* ~/.agents/skills/
```

```bash
export OPENAI_API_KEY="your-key"
cd <your-project> && codex
```

Use `$skill-name` or natural language. For deploy & monitor, switch to Claude Code.

### Tool Comparison

| Feature | Claude Code | Cursor (v2.4+) | Codex |
|---------|:-----------:|:---------------:|:-----:|
| Skills directory | `.claude/skills/` | `.cursor/skills/` | `.agents/skills/` |
| Slash commands | `/skill-name` | `/skill-name` | `$skill-name` |
| Auto API calls | Yes | No | Limited |
| Full deploy pipeline | Yes | No | No |
| Monitoring/Logs | Yes | No | No |
| **Best for** | **Full lifecycle** | **Writing code** | **Writing code** |

> **Recommended workflow:** Write code in **Cursor**, deploy & monitor with **Claude Code**.

---

## Skills Reference

### Lifecycle Overview

```
Scaffold → Configure → Code → Test → Deploy → Monitor → Teardown
```

### All Skills

| # | Command | What it does |
|---|---------|-------------|
| 1 | `/agentbase-wizard` | Full lifecycle wizard — start here if you're new |
| 2 | `/agentbase` | Platform reference & getting-started guide |
| 3 | `/agentbase-manage` | Manage agent identities, external auth, and memory |
| 4 | `/aip` | Create API keys & browse LLM models on AI Platform |
| 5 | `/agentbase-deploy` | Deploy pipeline, runtime management, and container registry (vCR) |
| 6 | `/agentbase-monitor` | Logs, metrics, and resource dashboard |
| 7 | `/agentbase-teardown` | Delete all resources for a project (with dry-run) |

### Subcommands

**`/agentbase-wizard`** — `[init|test|resume|step-N|reset]`
- `init [name] [--langgraph]` — Scaffold a new agent project
- `test [validate|local|docker|preflight]` — Test before deploy
- `resume` — Continue from last completed step
- *(no args)* — Start full 9-step wizard

**`/agentbase-manage`** — `<identity|auth|memory> <operation> [name]`
- `identity` — Register/manage agent identities
- `auth` — Store API keys & OAuth2 credentials for external services
- `memory` — Conversation history & long-term memory stores

**`/agentbase-deploy`** — `<deploy|runtime|vcr> [operation] [id-or-name]`
- `deploy` — End-to-end: build → push → deploy → verify
- `runtime` — CRUD runtimes, endpoints, versions, autoscaling
- `vcr` — Manage Docker repos & robot accounts on vCR registry

**`/agentbase-monitor`** — `<runtime-logs|endpoint-logs|metrics|dashboard> [runtime-id]`
- `runtime-logs` — View runtime container logs
- `endpoint-logs` — View endpoint-specific logs
- `metrics` — CPU/RAM usage metrics
- `dashboard` — Unified status of all platform resources

**`/aip`** — `<api-keys|models> <operation> [name-or-uuid]`
- `api-keys` — Create, list, get, update, delete API keys
- `models` — Browse, enable, disable, rate-limit LLM models

### Grouped by Stage

```
┌─────────────────────────────────────────────────────────┐
│  GETTING STARTED                                        │
│  /agentbase-wizard ─── Start here (guided A→Z)          │
│  /agentbase ────────── Platform reference                │
├─────────────────────────────────────────────────────────┤
│  BUILD & CONFIGURE                                      │
│  /agentbase-wizard init  Scaffold project                │
│  /agentbase-manage ───── Identity, auth, memory          │
│  /aip ────────────────── API keys & LLM models           │
├─────────────────────────────────────────────────────────┤
│  TEST & DEPLOY                                          │
│  /agentbase-wizard test  Test before deploy              │
│  /agentbase-deploy ───── Deploy pipeline & runtimes      │
├─────────────────────────────────────────────────────────┤
│  OPERATE                                                │
│  /agentbase-monitor ──── Logs, metrics, dashboard        │
├─────────────────────────────────────────────────────────┤
│  ADVANCED                                               │
│  /agentbase-deploy vcr ─ Container Registry              │
│  /agentbase-teardown ─── Delete resources                │
└─────────────────────────────────────────────────────────┘
```

---

## Practical Examples

### Create a Chatbot from Zero

```bash
/agentbase-wizard init my-chatbot --langgraph   # Scaffold
/aip api-keys create my-chatbot-key             # LLM access
/agentbase-manage memory create                 # Memory (optional)
/agentbase-wizard test local                    # Test
/agentbase-deploy deploy                        # Deploy
/agentbase-monitor runtime-logs <id>            # Monitor
```

### First Time? Use the Wizard

```bash
/agentbase-wizard              # Follow 9 steps from setup to deploy
/agentbase-wizard resume       # Come back later and continue
```

### Debug a Failing Agent

```bash
/agentbase-monitor runtime-logs <runtime-id>
/agentbase-monitor endpoint-logs <runtime-id> <endpoint-id>
/agentbase-monitor metrics <runtime-id>
```

### Tear Down a Project

```bash
/agentbase-teardown my-chatbot --dry-run    # Preview first
/agentbase-teardown my-chatbot              # Delete all resources
```

---

## FAQ & Troubleshooting

**Skills not showing up?**
- Verify skills directory exists (`.claude/skills/`, `.cursor/skills/`, or `.agents/skills/`)
- Each skill must have a valid `SKILL.md` with `name` and `description` frontmatter
- Restart the tool

**"401 Unauthorized" error?**
- Check `GREENNODE_CLIENT_ID` and `GREENNODE_CLIENT_SECRET` are set
- Verify Service Account has required IAM policies

**"OOMKilled" during deployment?**
- Choose a larger flavor when creating the runtime
- Optimize code to reduce memory usage

**How to resume a session?**
- State is saved in `.agentbase-state.json` — use `/agentbase-wizard resume` or any skill will detect existing state

---

## Important Notes

1. **Verify credentials first** – most errors are caused by missing IAM credentials
2. **Run `/agentbase-wizard test validate`** before deploying
3. **Use `--dry-run`** with teardown to preview before deleting
4. **Never commit `.env` files** – only commit `.env.example`
5. **Use the wizard** (`/agentbase-wizard`) if it's your first time
