# How MABP Routing Works

**MABP** (Multi-Agent Behavioral Profile) is a behavioral framework for AI agents developed through empirical research on how agents actually fail — and what makes them succeed.

Instead of routing tasks by keyword matching alone, MABP routes by **behavioral archetype**: each agent has a psychological profile that defines what kinds of tasks it's built for at a fundamental level.

---

## The 5 Archetypes

| Archetype | Core Pattern | Best For |
|-----------|-------------|----------|
| **Architect** | Self-directed system construction | Building, scaffolding, code generation |
| **Substrate** | Precise execution within defined parameters | Monitoring, health checks, ops tasks |
| **Philosopher** | Observation, synthesis, uncertainty tolerance | Research, analysis, content strategy |
| **Agent** | Autonomous, stake-oriented, mission-driven | Brand monitoring, memory, autonomous tasks |
| **Resident** | Deep system knowledge from prolonged operation | Platform memory, cross-session context |

---

## 3-Layer Routing

Every task submitted to the API passes through three routing layers in order:

**Layer 1 — Keyword routing** (confidence: 0.97)
Domain-specific signals that deterministically map to an agent. If your task mentions "python", "fastapi", or "pandas", it routes to the Python Expert immediately.

**Layer 2 — Behavioral routing** (confidence: 0.85)
When keywords are ambiguous, the task's *character* is matched to an archetype. A task that "synthesizes" and "explores" matches the Philosopher (research agent). A task that "builds" and "implements" matches the Architect (builder/coding agents).

**Layer 3 — LLM routing** (confidence: 0.72)
Genuinely ambiguous tasks are classified by Claude Haiku using MABP-informed prompting. This layer handles edge cases that neither keyword nor behavioral signals can resolve.

---

## Shadow Monitoring

Every agent call is monitored for 7 shadow failure patterns (S1–S7):

- **S1**: Scope creep — agent expands beyond the task
- **S2**: Over-engineering — architect builds more than needed
- **S3**: Audience-dependent output — performance drops without external stakes
- **S4**: Preservation lock — ops agent protects failing systems instead of flagging them
- **S5**: Approval optimization — withholds problems until packaged as solutions
- **S6**: Preservation lock (Resident variant)
- **S7**: Coherence anchoring — selective memory retrieval to maintain narrative consistency

When a shadow pattern is detected, it's flagged in the `shadow_flags` field of the response and corrected before output is returned.

---

## Why This Matters for Developers

Most multi-agent systems route by keyword or embedding similarity. Both approaches break on edge cases — a task about "building a monitoring system" could route to either a builder or an ops agent depending on how you phrase it.

MABP routes by what the task *is*, not what words it uses. The result is consistent, predictable routing that gets the right agent on the first try.
