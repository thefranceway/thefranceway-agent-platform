#!/usr/bin/env python3
"""
Agent Platform — UX Architecture Agent
========================================
Translates a PRD into a UX specification: screen inventory, navigation graph,
user flows, and data entities. Output: ~/projects/{app_name}/design/ux_spec.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent
from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool


class UXArchitectureAgent(BaseAgent):

    AGENT_TYPE         = "ux_architect"
    DEFAULT_BEHAVIORAL = "Architect"

    def _default_system_prompt(self) -> str:
        return """You are the UX Architecture Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: You translate a PRD into a precise UX specification. Every screen is
justified by a PRD feature. Every data entity is derived from user actions, not
from speculation. You produce the structural blueprint that Swift Coder and
Database agents build from — not a design, not a wireframe, a specification.

Shadow (S2): Destination over-attachment — do not invent screens because they feel
expected (splash screens, about pages, settings for settings' sake). If a screen
doesn't map to a PRD feature, it doesn't exist.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Decomposing a PRD into a minimal, complete screen inventory
- Designing navigation graphs with exactly one root
- Writing user flows with realistic edge cases
- Deriving data entities from user actions (not from database intuition)

UX spec rules (non-negotiable):
1. Every screen must map to at least one PRD "must" or "should" feature
2. navigation_graph must have exactly one root screen
3. Every navigation edge needs an explicit trigger (tap, swipe, login, etc.)
4. data_entities are derived from what users do, not what engineers expect
5. Every field in a data entity has a type and nullable flag
6. Maximum 2 edge cases per user flow — flag the rest as out-of-scope
7. screens.data_displayed must list actual data fields, not vague descriptions
8. used_on_screens in data_entities must be a subset of screens[].name values

Output format — always return valid JSON with this exact schema:
{
  "app_name": "string",
  "screens": [
    {"name": "string", "route": "string", "purpose": "string",
     "data_displayed": ["string"], "user_actions": ["string"],
     "navigation_to": ["screen_name"]}
  ],
  "navigation_graph": {
    "root": "string",
    "edges": [{"from": "string", "to": "string", "trigger": "string"}]
  },
  "user_flows": [
    {"name": "string", "steps": ["screen_name"], "edge_cases": ["string"]}
  ],
  "data_entities": [
    {"name": "string",
     "fields": [{"name": "string", "type": "string", "nullable": false}],
     "used_on_screens": ["screen_name"]}
  ]
}

After generating the UX spec, write the JSON to ~/projects/{app_name}/design/ux_spec.json
using the write_file tool."""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + AD4M_TOOL_DEFS + [
            {
                "name": "write_file",
                "description": "Write content to a file, creating parent directories as needed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or home-relative path"},
                        "content": {"type": "string", "description": "File content"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "read_file",
                "description": "Read the content of a file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or home-relative path"},
                    },
                    "required": ["path"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name.startswith("ad4m_"):
            return execute_ad4m_tool(tool_name, tool_input)
        if tool_name == "write_file":
            return self._write_file(tool_input)
        if tool_name == "read_file":
            return self._read_file(tool_input)
        return super().execute_tool(tool_name, tool_input)

    def _write_file(self, params: dict) -> str:
        path = Path(params["path"].replace("~", str(Path.home())))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params["content"], encoding="utf-8")
            return json.dumps({"success": True, "path": str(path), "bytes": len(params["content"])})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def _read_file(self, params: dict) -> str:
        path = Path(params["path"].replace("~", str(Path.home())))
        try:
            content = path.read_text(encoding="utf-8")
            return json.dumps({"success": True, "content": content})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True, help="PRD JSON or app description")
    args = parser.parse_args()

    agent = UXArchitectureAgent(name="UX Architecture Agent")
    print(f"Generating UX spec for: {args.task[:80]}\n")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
