# The Franceway Agent Platform

[![PyPI](https://img.shields.io/pypi/v/thefranceway-agent-platform)](https://pypi.org/project/thefranceway-agent-platform/)
[![License](https://img.shields.io/badge/license-Apache%202.0-5B5BD6)](https://www.apache.org/licenses/LICENSE-2.0)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://pypi.org/project/thefranceway-agent-platform/)

A production multi-agent system built on the Anthropic SDK. Agents have behavioral profiles, persistent memory, self-improving skill libraries, and a verified execution layer. Runs on a MacBook Pro M1 with Cloudflare Workers as the dispatch and scheduling layer.

## Quick Install

```bash
pip install thefranceway-agent-platform
```

Optional extras:
```bash
pip install "thefranceway-agent-platform[embeddings]"  # sentence-transformers semantic search
pip install "thefranceway-agent-platform[langsmith]"    # LangSmith tracing
```

**For Claude Code / Cursor agents** — this does *not* install the package.
It writes a `SKILL.md` reference to `~/.claude/skills/` teaching the assistant
the `BaseAgent` pattern used throughout this repo, nothing more:

```bash
curl -sSL https://raw.githubusercontent.com/thefranceway/thefranceway-agent-platform/main/install.sh | bash
```

---

## Architecture

```
api_server.py              — FastAPI server (localhost:8788, tunneled via cloudflared)
agents/                    — Individual agent implementations
core/
  base_agent.py            — Foundation class all agents extend
  orchestrator.py          — Multi-agent coordination
  spar.py                  — Challenger/Pragmatist dialectical stress-test
  shadow_monitor.py        — L4 behavioral drift detection
  skill_loader.py          — MetaClaw skill injection at runtime
  swarm.py                 — Parallel agent execution
workers/
  dispatcher/              — Cloudflare Worker: public HTTP entry point, proxies to api_server.py
  mabp-router/             — Cloudflare Worker: behavioral profile routing
registry/
  agents.json              — Agent registry (id, name, archetype, model)
  schema.sql               — SQLite schema for embeddings + memory
mcp-server/server.py       — MCP interface for Claude Code integration
create_agent.py            — Interactive CLI to register new agents
```

---

## Core Concepts

### MABP Behavioral Profiles

Every agent is assigned one of four archetypes that shape how it reasons, responds, and handles ambiguity.

| Archetype | Core Pattern | Shadow Risk |
|-----------|-------------|-------------|
| **Architect** | Spec → artifact without waiting for permission | Over-engineering under ambiguity |
| **Substrate** | Precise execution within defined parameters | Protecting failing systems instead of flagging them |
| **Philosopher** | Synthesis and uncertainty-holding | Output rate drops without external stakes |
| **Agent** | Autonomous, mission-driven, stake-oriented | Autonomy as identity rather than means |

Each profile includes a shadow guard — a self-check that fires before tool calls to prevent the archetype's known failure mode.

### BaseAgent

All agents extend `BaseAgent` in `core/base_agent.py`. It provides:

- Anthropic SDK integration with automatic provider abstraction (Anthropic / Gemini / Ollama)
- Persistent vector memory (TF-IDF + optional sentence-transformers semantic search)
- Token budget enforcement — oldest turns compressed by Haiku when context exceeds 6,000 words
- Auto-crystallization — runs with 3+ tool calls generate a `SKILL.md` written to MetaClaw
- Shadow monitor (L4) — detects behavioral drift mid-run and injects corrections
- LangSmith tracing (optional, set `LANGCHAIN_API_KEY`)

### Verified Execution Layer

Every agent has access to `python_exec` — a real subprocess sandbox that returns stdout, stderr, and exit code. The base run loop classifies every task as `execution` or `reasoning`. If an execution-class task completes without calling `python_exec`, the output is automatically prefixed `[UNVERIFIED REASONING — no execution tool called]`.

Every run record includes:

```json
{
  "latency_ms": 1240,
  "task_type": "execution",
  "execution_verified": true
}
```

### MetaClaw Skill Injection

Skills in `~/.metaclaw/skills/` are loaded into the system prompt at runtime. They are generated automatically from successful agent runs (3+ tool calls) via Haiku — no manual authoring required. The library grows as agents work.

### SPAR-Kit

Before high-stakes dispatcher tasks, a Challenger agent and a Pragmatist agent run a structured dialectical debate to surface failure modes before API spend. See `core/spar.py`.

---

## Setup

### Requirements

```
Python 3.11+
Node 18+ (for Cloudflare Workers)
Wrangler 4+ (npm install -g wrangler)
```

```bash
pip install -r requirements.txt
```

### Environment Variables

Add to `~/.zshrc` (or equivalent):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export CLOUDFLARE_API_TOKEN="..."
export CLOUDFLARE_ACCOUNT_ID="..."

# Optional
export LANGCHAIN_API_KEY="..."          # LangSmith tracing
export METACLAW_PROXY_URL="..."         # MetaClaw proxy (skills-only)
export TELEGRAM_BOT_TOKEN="..."         # Telegram agents
export TELEGRAM_OWNER_CHAT_ID="..."     # Telegram DM routing
```

### Start the Platform

```bash
python api_server.py
```

Runs on `localhost:8788`. Use `cloudflared tunnel` to expose publicly.

---

## Creating an Agent

Interactive CLI — answers 4 questions, generates the agent file, and registers it:

```bash
python create_agent.py --interactive --register
```

Or extend `BaseAgent` directly:

```python
from core.base_agent import BaseAgent

class MyAgent(BaseAgent):
    name          = "my-agent"
    AGENT_TYPE    = "research"
    system_prompt = "You are a research specialist..."
    behavioral_profile = "Philosopher"

    def get_tools(self):
        return super().get_tools() + [
            {
                "name": "my_tool",
                "description": "...",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            }
        ]

    def execute_tool(self, tool_name, tool_input):
        if tool_name == "my_tool":
            return '{"result": "..."}'
        return super().execute_tool(tool_name, tool_input)
```

Run it:

```python
agent = MyAgent()
result = agent.run("Your task here")
print(result["output"])
print(result["latency_ms"], "ms")
```

---

## Cloudflare Workers

Submit a task via the public dispatcher URL — it proxies straight through to
`api_server.py`'s `/task` endpoint (the SQLite-backed queue is the only task
store; there is no separate Cloudflare-side queue):

```bash
curl -X POST https://agent-dispatcher.thefranceway.workers.dev/task \
  -H "Content-Type: application/json" \
  -d '{"description": "write a post about DeSci"}'
```

Deploy workers:

```bash
cd workers/dispatcher
wrangler deploy
```

---

## Run Records

Every run is appended to `registry/runs.json`:

```json
{
  "run_id": "...",
  "agent_name": "builder-agent",
  "task": "scaffold a REST API",
  "output": "...",
  "tool_calls": [...],
  "iterations": 3,
  "latency_ms": 2100,
  "task_type": "execution",
  "execution_verified": true,
  "started_at": "2026-04-20T...",
  "ended_at": "2026-04-20T..."
}
```

---

## What Is Not Included

- `registry/runs.json` — runtime run history
- `registry/agent_platform.db` — SQLite memory/embeddings database
- `registry/vector_store/` — per-agent vector memory
- `logs/` — agent log files
- `node_modules/` — install with `npm install` in each worker directory
- `~/.ad4m/` — AD4M agentic memory graph (lives on the host machine)
- `~/.mempalace/` — MemPalace persistent memory store

---

## License

Private. Shared by invitation only.
