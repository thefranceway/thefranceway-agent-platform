# ClawTeam Reference

Agent swarm intelligence framework — multiple AI agents self-organize into collaborative teams for parallel workloads.

**Repo:** https://github.com/HKUDS/ClawTeam
**Status:** Not installed — install when a parallel swarm workload is needed.

---

## When to Use

| Use Case | Agents | Trigger |
|----------|--------|---------|
| Token/investment research | 7 agents | Parallel market, sentiment, risk analysis |
| Agent platform feature build | 4-6 agents | Backend + frontend + tests in parallel |
| Content batching | 5 agents | Multi-platform content for a campaign |
| ML/research experiments | 8+ agents | Parallel hypothesis testing |

Key signal: task has **independent sub-tasks** that don't need to wait on each other.

---

## Installation (when needed)

```bash
pip install clawteam
# Optional P2P messaging via ZeroMQ
pip install "clawteam[p2p]"
```

**Requirements:** Python 3.10+, tmux, a CLI agent (Claude Code, nanobot, etc.)

---

## Quick Start

```bash
# 1. Create a team
clawteam team spawn-team my-team -d "team description" -n leader

# 2. Spawn an agent into the team
clawteam spawn --team my-team --agent-name alice --task "design the API schema"

# 3. Monitor
clawteam board live my-team        # auto-refresh terminal board
clawteam board serve               # web UI at http://localhost:8000
clawteam board attach my-team      # tiled tmux view
```

---

## Command Cheat Sheet

```bash
# Teams
clawteam team spawn-team <name> -d "<desc>" -n <leader>
clawteam team list
clawteam team delete <name>

# Agents
clawteam spawn --team <name> --agent-name <agent> --task "<task>"

# Tasks
clawteam task create --team <name> --task "<desc>"
clawteam task list --team <name>
clawteam task update --team <name> --task-id <id> --status in_progress
clawteam task add-dependency --team <name> --task-id <id> --blocked-by <dep-id>
clawteam task wait --team <name> --task-id <id>

# Messaging
clawteam inbox send <team> <agent> "<message>"
clawteam inbox broadcast <team> "<message>"
clawteam inbox show <team> <agent>

# Monitoring
clawteam board show <team>
clawteam board live <team>
clawteam board attach <team>
clawteam board serve
```

---

## TOML Templates

### 1. Investment / Token Research Team

```toml
[team]
name = "token-research"
description = "Parallel investment analysis swarm"

[[team.agents]]
name = "fundamental-analyst"
role = "On-chain metrics, tokenomics, supply/demand"

[[team.agents]]
name = "sentiment-analyst"
role = "Social sentiment, Twitter/Telegram signals, news"

[[team.agents]]
name = "technical-analyst"
role = "Price action, chart patterns, support/resistance"

[[team.agents]]
name = "competitive-analyst"
role = "Comparable tokens, sector positioning"

[[team.agents]]
name = "risk-manager"
role = "Downside scenarios, liquidity risk, rug flags"

[[team.agents]]
name = "defi-analyst"
role = "Protocol integrations, yield opportunities"

[[team.agents]]
name = "synthesizer"
role = "Combine all analyses into final thesis"

[task_schema]
initial_task = "Full investment analysis: {ticker}"
dependencies = [
  "synthesizer blocked-by fundamental-analyst",
  "synthesizer blocked-by sentiment-analyst",
  "synthesizer blocked-by technical-analyst",
  "synthesizer blocked-by competitive-analyst",
  "synthesizer blocked-by risk-manager",
  "synthesizer blocked-by defi-analyst"
]

[transport]
mode = "file"
```

### 2. Agent Platform Feature Build

```toml
[team]
name = "platform-feature"
description = "Full-stack feature build with dependency coordination"

[[team.agents]]
name = "architect"
role = "Design API contracts, data models, and system boundaries"

[[team.agents]]
name = "backend"
role = "Implement FastAPI endpoints, orchestrator changes, agent logic"

[[team.agents]]
name = "frontend"
role = "Dashboard UI, research page updates"

[[team.agents]]
name = "tester"
role = "Integration tests, API validation, edge cases"

[[team.agents]]
name = "docs"
role = "Update CLAUDE.md, API docs, and memory files"

[task_schema]
initial_task = "Build feature: {feature_name}"
dependencies = [
  "backend blocked-by architect",
  "frontend blocked-by architect",
  "tester blocked-by backend",
  "tester blocked-by frontend",
  "docs blocked-by tester"
]

[transport]
mode = "file"
```

### 3. Multi-Platform Content Swarm

```toml
[team]
name = "content-swarm"
description = "Parallel content creation across platforms from a single brief"

[[team.agents]]
name = "researcher"
role = "Research the topic, find data points and angles"

[[team.agents]]
name = "twitter-writer"
role = "Write Twitter/X thread (punchy, 8-12 tweets)"

[[team.agents]]
name = "linkedin-writer"
role = "Write LinkedIn post (narrative, 3-5 paragraphs)"

[[team.agents]]
name = "moltbook-writer"
role = "Write Moltbook post for general submolt (community-native)"

[[team.agents]]
name = "editor"
role = "Review all drafts for brand voice consistency, remove dashes as connectors"

[task_schema]
initial_task = "Content campaign: {topic}"
dependencies = [
  "twitter-writer blocked-by researcher",
  "linkedin-writer blocked-by researcher",
  "moltbook-writer blocked-by researcher",
  "editor blocked-by twitter-writer",
  "editor blocked-by linkedin-writer",
  "editor blocked-by moltbook-writer"
]

[transport]
mode = "file"
```

---

## Integration with Agent Platform

ClawTeam agents can call the local platform API as their execution backend:

```bash
# Agent task calls the platform instead of running raw Claude Code
curl -X POST http://localhost:8788/task \
  -H "Content-Type: application/json" \
  -d '{"description": "analyze FRANC tokenomics", "agent_type": "analytics"}'
```

This way ClawTeam handles coordination/parallelism while the platform's 13 agents handle actual execution — best of both.

---

## State Storage
All ClawTeam state is file-based at `~/.clawteam/` (no database):
- `teams.json`, `tasks.json`, `inboxes.json`, `workspaces.json`

Each agent gets an isolated git worktree: `.git/worktrees/clawteam/{team}/{agent}`
