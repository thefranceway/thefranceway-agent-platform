# TencentDB Memory Layer

## Architecture

The TencentDB Memory Layer provides persistent semantic memory storage for agents through a proxy-to-core-to-AD4M architecture:

```
Agent Request
    ↓
tdai-proxy (port 8096)                 — OpenAI-compatible API facade
    ↓
memory-core (port 8420)                — Memory orchestration layer
    ↓
AD4M (local GraphQL executor)          — Semantic memory graph
```

This enables agents to query and write to persistent memory through standard OpenAI SDK calls, with the memory-core layer translating requests into AD4M link operations.

---

## Critical Configuration

### tdai-proxy Environment

The proxy **must** include `/v1` in the upstream URL. This is non-negotiable:

```bash
UPSTREAM_URL=https://api.anthropic.com/v1
```

**Wrong:**
```bash
UPSTREAM_URL=https://api.anthropic.com      # Missing /v1 — will fail
```

### Model Specification

All requests must specify the exact model:

```bash
model: claude-sonnet-4-5-20250929
```

This matches the Anthropic model identifier and ensures proper routing through the memory-core layer.

### API Key Consistency

The API key used by clients **must** match the key in:

- `/Users/multiuniverse/TencentDB-Agent-Memory/deploy/global-images/.env`
- Under `PROXY_UPSTREAM_API_KEY`
- Must also match `ANTHROPIC_AUTH_TOKEN` when set

**Configuration:**
```bash
# In .env — use actual key from PROXY_UPSTREAM_API_KEY
# (Do not use placeholder keys)

# In agent config or skill
api_key = "<value from .env PROXY_UPSTREAM_API_KEY>"
```

---

## Integration Points

### 1. Skill Integration

Skills can invoke memory operations through the proxy:

```python
from openai import OpenAI
import os

# API key must match .env PROXY_UPSTREAM_API_KEY
client = OpenAI(
    base_url="http://localhost:8096/v1",
    api_key=os.getenv("ANTHROPIC_AUTH_TOKEN")  # or read from .env
)

response = client.chat.completions.create(
    model="claude-sonnet-4-5-20250929",
    messages=[
        {"role": "user", "content": "Recall all facts about project X"}
    ]
)
```

### 2. Knowledge Base Access

The memory-core layer exposes knowledge base queries through the same interface:

```python
# Query knowledge base
response = client.chat.completions.create(
    model="claude-sonnet-4-5-20250929",
    messages=[
        {"role": "system", "content": "knowledge_base: desci-modules"},
        {"role": "user", "content": "What are the key concepts in module 3?"}
    ]
)
```

### 3. Direct AD4M Memory Writes

For write operations, the memory-core translates completion requests into AD4M link expressions:

```python
# Write a memory (creates AD4M link)
response = client.chat.completions.create(
    model="claude-sonnet-4-5-20250929",
    messages=[
        {"role": "system", "content": "action: write_memory"},
        {"role": "user", "content": "source: agent://session/2026-08-17, predicate: ad4m://remembers, target: literal://TencentDB Memory Layer configured"}
    ]
)
```

The memory-core layer parses the structured request and calls `ad4m.perspective.addLink()`.

---

## Starting the Stack

### Option 1: Direct Script

```bash
cd /path/to/memory-core
./start-proxy.sh
```

This script:
1. Verifies AD4M is running
2. Starts memory-core on port 8420
3. Starts tdai-proxy on port 8096
4. Tails logs from both processes

### Option 2: Shell Alias

Add to `~/.zshrc`:

```bash
alias claudemem='cd ~/Code/memory-core && ./start-proxy.sh'
```

Then run:

```bash
claudemem
```

---

## Verification

Once running, verify the stack:

### 1. Check tdai-proxy health

```bash
curl http://localhost:8096/health
```

Expected response:
```json
{"status": "healthy", "upstream": "https://api.anthropic.com/v1"}
```

### 2. Check memory-core health

```bash
curl http://localhost:8420/v1/health
```

Expected response:
```json
{"status": "ok", "ad4m_connected": true}
```

### 3. Test end-to-end

```bash
# Use actual key from .env PROXY_UPSTREAM_API_KEY
curl -X POST http://localhost:8096/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" \
  -d '{
    "model": "claude-sonnet-4-5-20250929",
    "messages": [
      {"role": "user", "content": "Recall all facts about thefranceway-agent-platform"}
    ]
  }'
```

If this returns structured memory results, the full stack is operational.

---

## Troubleshooting

### Error: "Connection refused" on port 8420

**Cause:** memory-core is not running.

**Fix:**
```bash
cd ~/Code/memory-core
npm run dev
```

### Error: "404 Not Found" from tdai-proxy

**Cause:** `UPSTREAM_URL` is missing `/v1`.

**Fix:** Update tdai-proxy `.env`:
```bash
UPSTREAM_URL=https://api.anthropic.com/v1
```

### Error: "Invalid API key"

**Cause:** Key mismatch between client and proxy.

**Fix:** Ensure client uses the correct key from `.env`:
```bash
# Read from /Users/multiuniverse/TencentDB-Agent-Memory/deploy/global-images/.env
# Under PROXY_UPSTREAM_API_KEY (must match ANTHROPIC_AUTH_TOKEN)

# Client config
api_key = os.getenv("ANTHROPIC_AUTH_TOKEN")
```

### Error: "Model not found"

**Cause:** Wrong model identifier.

**Fix:** Use the exact model name:
```python
model="claude-sonnet-4-5-20250929"
```

---

## Architecture Notes

### Why the Proxy Layer?

The tdai-proxy provides an OpenAI-compatible API surface, allowing agents built on the OpenAI SDK to use AD4M memory without code changes. Agents send standard completion requests; the proxy forwards them to memory-core, which interprets them as memory operations.

### Why `/v1` Is Required

The Anthropic API expects routes with the `/v1` prefix (`/v1/messages`, etc.). Without `/v1` in the upstream URL, tdai-proxy sends malformed requests (`/messages` instead of `/v1/messages`), which the API rejects as invalid routes.

### AD4M as the Memory Graph

AD4M stores all memory as signed RDF-style link expressions:

```
(source, predicate, target)
```

Example:
```
(agent://session/2026-08-17, ad4m://knows, literal://TencentDB Memory Layer is running)
```

This graph structure enables multi-hop traversal, semantic queries, and cross-session memory sharing.

---

## Integration with BaseAgent

Agents extending `BaseAgent` can use the TencentDB Memory Layer through the skill interface:

```python
from core.base_agent import BaseAgent

class MyAgent(BaseAgent):
    name = "my-agent"
    system_prompt = "You are a research agent with persistent memory."
    
    def run(self, task):
        # Automatic memory recall via skill
        result = super().run(task)
        
        # Memory is written to AD4M via memory-core after successful runs
        return result
```

The `BaseAgent` class automatically integrates with the memory layer when the `tdai-memory` skill is loaded from MetaClaw.

---

## Security Notes

- **API Key:** Use the production key from `.env` (`PROXY_UPSTREAM_API_KEY`). Rotate regularly and never commit to version control.
- **Port Binding:** Both 8096 and 8420 bind to `localhost` only — not exposed externally.
- **AD4M Keystore:** Memory writes are signed by the local AD4M agent's DID. Protect the keystore.

---

## Further Reading

- [AD4M Documentation](https://docs.ad4m.dev)
- [memory-core Repository](https://github.com/thefranceway/memory-core) (private)
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference)
