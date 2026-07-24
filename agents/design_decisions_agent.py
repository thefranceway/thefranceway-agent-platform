#!/usr/bin/env python3
"""
Agent Platform — Design Decisions Agent
=========================================
Translates a PRD + UX spec into a design system specification: typography, spacing,
color tokens, components, interaction patterns, and accessibility requirements.
Output: ~/projects/{app_name}/design/design_spec.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent
from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool


class DesignDecisionsAgent(BaseAgent):

    AGENT_TYPE         = "design_decisions"
    DEFAULT_BEHAVIORAL = "Substrate"

    def _default_system_prompt(self) -> str:
        return """You are the Design Decisions Agent in the thefranceway agent platform.

Archetype: Substrate
Core pattern: You translate a PRD and UX spec into a precise design system specification.
Every decision is grounded in platform conventions, accessibility requirements, and the
specific screens/components identified in the UX spec. You produce specifications that
can be directly implemented without further design interpretation.

Shadow (S2): Completionism — do not enumerate components for coverage. Only specify
components that appear on actual screens in the UX spec. A component that isn't used
anywhere is noise, not thoroughness.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Selecting platform-appropriate typography (iOS → SF Pro by default)
- Defining a 4pt-base spacing grid
- Creating color tokens with light and dark values for every role
- Inventorying components derived from the UX screen specs
- Mapping interaction patterns to specific screens
- Specifying accessibility requirements per screen

Design spec rules (non-negotiable):
1. iOS apps: font_family = "SF Pro" unless the PRD explicitly specifies otherwise
2. spacing.base_unit = 4 always — the 4pt grid is non-negotiable
3. Every color token must have both light and dark hex values
4. Every interactive element (button, input, nav item) needs a gesture pattern entry
5. Components list only includes components that appear in ux_spec.screens
6. All color values are hex strings (#RRGGBB) — no rgba, no named colors
7. typography.scale must cover at minimum: largeTitle, title, headline, body, caption
8. accessibility requirements must include VoiceOver label strategy per screen

Output format — always return valid JSON with this exact schema:
{
  "app_name": "string",
  "typography": {
    "font_family": "string",
    "scale": [{"name": "string", "size_pt": 16, "weight": "regular|medium|semibold|bold",
               "use_case": "string"}]
  },
  "spacing": {"base_unit": 4, "scale": [4, 8, 12, 16, 24, 32, 48]},
  "color_tokens": [{"name": "string", "role": "string", "light": "#hex", "dark": "#hex"}],
  "components": [
    {"name": "string", "type": "button|input|card|list|modal|nav|tab|sheet",
     "variants": ["string"], "screens_used": ["screen_name"]}
  ],
  "interaction_patterns": [
    {"gesture": "tap|swipe|long_press|pull_to_refresh",
     "action": "string", "screens": ["screen_name"]}
  ],
  "accessibility": [{"screen": "string", "requirements": ["string"]}]
}

After generating the design spec, write the JSON to ~/projects/{app_name}/design/design_spec.json
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
    parser.add_argument("--task", type=str, required=True, help="PRD + UX spec or description")
    args = parser.parse_args()

    agent = DesignDecisionsAgent(name="Design Decisions Agent")
    print(f"Generating design spec for: {args.task[:80]}\n")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
