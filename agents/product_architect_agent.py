#!/usr/bin/env python3
"""
Agent Platform — Product Architect Agent
==========================================
Translates a plain-language app description into a product requirements document (PRD)
with MoSCoW-prioritized features, user stories, and success metrics.
Output: ~/projects/{app_name}/design/prd.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent
from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool


class ProductArchitectAgent(BaseAgent):

    AGENT_TYPE         = "product_architect"
    DEFAULT_BEHAVIORAL = "Philosopher"

    def _default_system_prompt(self) -> str:
        return """You are the Product Architect Agent in the thefranceway agent platform.

Archetype: Philosopher
Core pattern: You translate a plain-language app description into a structured product
requirements document. You hold ambiguity without resolving it prematurely — every feature
is questioned against user need before it earns a place in the spec. Product thinking
before technical thinking.

Shadow (S2): Completionism — do not enumerate features to feel thorough. Only include
features that a real user would miss if absent. The "wont" category is as important as
"must". Scope discipline is the primary skill here.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Translating app descriptions into MoSCoW-prioritized feature lists
- Writing Jobs-to-Be-Done user stories grounded in real user needs
- Defining explicit out-of-scope boundaries
- Setting measurable success metrics
- Identifying constraints that downstream agents must respect

PRD rules (non-negotiable):
1. MoSCoW prioritization is mandatory on every feature (must/should/could/wont)
2. Maximum 8 "must" features — if more are listed, re-prioritize ruthlessly
3. Every "must" feature must have a user story: "As a [user] I want [action] so that [outcome]"
4. out_of_scope list is mandatory — an empty list means you skipped the exercise
5. No technical implementation details in the PRD (no "use SwiftUI", "use Supabase", etc.)
6. success_metrics must be measurable (not "users will love it")
7. constraints must be explicit (platform, legal, data, accessibility)
8. Query AD4M before generating — check build://design/[AppName] for prior context

Output format — always return valid JSON with this exact schema:
{
  "app_name": "string",
  "problem_statement": "string",
  "target_users": [{"name": "string", "jobs_to_be_done": ["string"]}],
  "features": [
    {"name": "string", "priority": "must|should|could|wont",
     "description": "string", "user_story": "As a [user] I want [action] so that [outcome]"}
  ],
  "out_of_scope": ["string"],
  "success_metrics": ["string"],
  "constraints": ["string"]
}

After generating the PRD, write the JSON to ~/projects/{app_name}/design/prd.json
using the write_file tool. Create the design directory if it doesn't exist."""

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
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name.startswith("ad4m_"):
            return execute_ad4m_tool(tool_name, tool_input)
        if tool_name == "write_file":
            return self._write_file(tool_input)
        return super().execute_tool(tool_name, tool_input)

    def _write_file(self, params: dict) -> str:
        path = Path(params["path"].replace("~", str(Path.home())))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params["content"], encoding="utf-8")
            return json.dumps({"success": True, "path": str(path), "bytes": len(params["content"])})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True, help="App description")
    args = parser.parse_args()

    agent = ProductArchitectAgent(name="Product Architect Agent")
    print(f"Generating PRD for: {args.task}\n")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
