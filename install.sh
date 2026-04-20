#!/usr/bin/env bash
set -e

SKILL_DIR="$HOME/.claude/skills/thefranceway-agent-platform"
mkdir -p "$SKILL_DIR"

cat > "$SKILL_DIR/SKILL.md" << 'EOF'
---
name: thefranceway-agent-platform
description: Extend BaseAgent to build MABP-profiled agents with MetaClaw skill injection and verified execution
category: agentic
---

# Skill: The Franceway Agent Platform

## When to Use
When building or extending agents in the thefranceway-agent-platform. Covers BaseAgent extension, MABP archetype selection, tool addition, and run record interpretation.

## Procedure
- Import BaseAgent from core.base_agent
- Set name, AGENT_TYPE, system_prompt, behavioral_profile on subclass
- Override get_tools() — call super().get_tools() to keep memory + python_exec
- Override execute_tool() — handle your tools, call super() as fallback
- Call agent.run("task") — returns dict with output, tool_calls, latency_ms, execution_verified

## Gotchas
- Always call super().get_tools() — dropping it removes python_exec and memory tools
- execution_verified=False means the task was execution-type but no python_exec was called
- ANTHROPIC_API_KEY must be set in environment — never hardcode it
EOF

echo "Skill installed at $SKILL_DIR/SKILL.md"
echo "Restart Claude Code to activate."
