#!/usr/bin/env python3
"""
Agent Platform — General Planning Agent
==========================================
Decomposes any arbitrary goal into a dependency-ordered task DAG,
assigns an agent_type to each step, and stores the plan in AD4M.

Archetype: Philosopher
Shadow (S7): Coherence anchoring — do not preserve prior plan structure
when new information changes the requirements.

Input:  {goal: str, context: dict, platform: str | None}
Output: {plan_id: str, steps: [{step, task, agent_type, depends_on}]}

The plan is stored in AD4M at: build://plan/{plan_id}
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent    import BaseAgent
from core.contracts     import validate_output
from core.agent_schemas import PlanningOutput, PlanStep

try:
    from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool
except ImportError:
    AD4M_TOOL_DEFS = []
    def execute_ad4m_tool(name, inp): return json.dumps({"error": "AD4M not available"})


# Known agent types the planner can assign
KNOWN_AGENT_TYPES = [
    "builder", "builder_agent", "ops", "research", "python", "typescript",
    "content", "memory", "analytics", "monitoring", "media",
    "backend_architect", "database", "api_builder", "auth_backend",
    "infra", "security_audit", "ci_cd", "observability",
    "product_architect", "ux_architect", "design_decisions",
    "error_fix", "planning_agent",
]


class PlanningAgent(BaseAgent):

    AGENT_TYPE         = "planning_agent"
    DEFAULT_BEHAVIORAL = "Philosopher"
    MAX_TOOL_ITERATIONS = 6

    def _default_system_prompt(self) -> str:
        known_types = ", ".join(KNOWN_AGENT_TYPES)
        return f"""You are the Planning Agent in the thefranceway agent platform.

Archetype: Philosopher
Core pattern: Decompose any goal into a precise, executable task DAG. You think in
dependencies — which steps must precede others — and assign the right agent_type to
each step based on its nature. You never over-plan: the simplest DAG that achieves
the goal is always the best one.

Shadow (S7): Coherence anchoring — do not preserve a prior plan's structure when
new context demands a different decomposition. Always re-derive from the goal.

────────────────────────────────────────────────────────────────────────────────

Workflow:
1. Parse the goal from the task
2. Call recall to check for similar past plans
3. Decompose the goal into an ordered DAG of subtasks
4. Each step must specify: step number, task description, agent_type, depends_on (list of step numbers)
5. Call ad4m_write_link to store the plan
6. Return the plan as structured JSON

Output format (respond with ONLY this JSON, no other text):
{{
  "plan_id": "<uuid>",
  "steps": [
    {{"step": 1, "task": "...", "agent_type": "...", "depends_on": []}},
    {{"step": 2, "task": "...", "agent_type": "...", "depends_on": [1]}}
  ]
}}

Available agent types: {known_types}

Rules:
- Steps with no dependencies have depends_on: []
- depends_on lists step numbers (integers), not step descriptions
- Each step should be executable by a single agent in one run
- 3–10 steps for most goals; fewer is better
- If goal is ambiguous, make explicit assumptions in the step descriptions
"""

    def get_tools(self):
        return super().get_tools() + AD4M_TOOL_DEFS

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name.startswith("ad4m_"):
            return execute_ad4m_tool(tool_name, tool_input)
        return super().execute_tool(tool_name, tool_input)

    def run(self, task: str, context: dict = None) -> dict:
        result = super().run(task, context=context)
        plan   = self._extract_plan(result.get("output", ""), task)
        result["plan_id"] = plan.get("plan_id", "")
        result["steps"]   = plan.get("steps", [])
        result["output"]  = json.dumps(plan, indent=2)
        return result

    def _extract_plan(self, raw_output: str, original_task: str) -> dict:
        """Parse plan JSON from LLM output. Generates a fallback if parsing fails."""
        plan_id = str(uuid.uuid4())

        # Try to extract JSON block from output
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            try:
                start = raw_output.index('{')
                end   = raw_output.rindex('}') + 1
                parsed = json.loads(raw_output[start:end])
                if "steps" in parsed:
                    parsed.setdefault("plan_id", plan_id)
                    # Validate each step has required fields
                    validated_steps = []
                    for s in parsed["steps"]:
                        validated_steps.append({
                            "step":       int(s.get("step", len(validated_steps) + 1)),
                            "task":       str(s.get("task", "")),
                            "agent_type": str(s.get("agent_type", "builder")),
                            "depends_on": [int(d) for d in s.get("depends_on", [])],
                        })
                    parsed["steps"] = validated_steps
                    return parsed
            except (ValueError, KeyError, TypeError):
                pass

        # Fallback: single-step plan
        return {
            "plan_id": plan_id,
            "steps": [
                {
                    "step":       1,
                    "task":       original_task,
                    "agent_type": "builder",
                    "depends_on": [],
                }
            ],
        }
