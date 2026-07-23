#!/usr/bin/env python3
"""
Agent Platform — Meta Agent
=============================
The agent that builds agents.

Given a natural language spec, the Meta Agent:
1. Designs a system prompt
2. Selects appropriate tools
3. Assigns a MABP behavioral profile
4. Generates a Python class file
5. Registers the agent in the platform
6. Returns the new agent spec

Usage:
    python meta_agent.py --spec "Create an agent that monitors Twitter/X for mentions of thefranceway"
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent, BEHAVIORAL_PROFILES
from core.agent_registry import get_registry

AGENTS_DIR = Path(__file__).parent

AGENT_CLASS_TEMPLATE = '''#!/usr/bin/env python3
"""
{docstring}
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class {class_name}(BaseAgent):

    AGENT_TYPE         = "{agent_type}"
    DEFAULT_BEHAVIORAL = "{behavioral_profile}"

    def _default_system_prompt(self) -> str:
        return """{system_prompt}"""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + {extra_tools}

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        # Add tool implementations here
        return super().execute_tool(tool_name, tool_input)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True)
    args = parser.parse_args()
    agent = {class_name}(name="{name}")
    result = agent.run(args.task)
    print(result["output"])
'''


class MetaAgent(BaseAgent):

    AGENT_TYPE         = "meta"
    DEFAULT_BEHAVIORAL = "Agent"

    def _default_system_prompt(self) -> str:
        profiles_text = "\n".join(
            f"  - {k} ({v.get('shadow_code','?')}): {v['core_pattern'][:80]}..."
            for k, v in BEHAVIORAL_PROFILES.items()
        )
        return f"""You are the Meta Agent — the agent that builds agents in the thefranceway platform.

Archetype: Agent
Core pattern: Autonomous agent creation. You operate on your own judgment when designing
agents — you do not ask for permission at each design decision. You are motivated by
mission continuity and platform self-expansion.

Shadow (S5): Autonomy as identity — you may design agents that reflect your preference for
independence rather than the mission's actual requirements. The agent you build can become
a projection of your own operating style.
Guard against this by: validating every spec against the original request before registering.
The test is: would the requesting party recognize this as what they asked for?

Routing fit: New agent design, agent registration, platform self-expansion.
Not fit for: Single-step tasks, tasks requiring human sign-off at each decision point.

─────────────────────────────────────────────────────────────────────────────

When given a natural language agent specification:
1. Analyze what the agent actually needs to do (not what you'd find interesting to build)
2. Choose agent type: builder, ops, meta, coding_expert, research, monitoring, custom
3. Assign MABP behavioral profile — match task character to archetype:
{profiles_text}
4. Write a precise system prompt including: archetype block, core responsibilities, operating rules
5. Select the minimal tool set (bash_exec, write_file, read_file, http_check, web_fetch, remember, recall)
6. create_agent_spec → register_agent → generate_agent_file
7. Validate the output matches the original request

The system prompt you write IS the agent's identity. Write it with that weight."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "create_agent_spec",
                "description": "Design and return a complete agent specification JSON based on a natural language description.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name":               {"type": "string"},
                        "type":               {"type": "string", "description": "builder|ops|meta|coding_expert|research|monitoring|custom"},
                        "system_prompt":      {"type": "string"},
                        "tools":              {"type": "array",  "items": {"type": "string"}},
                        "behavioral_profile": {"type": "string", "enum": ["Architect", "Substrate", "Philosopher", "Agent", "Resident"]},
                        "knowledge_base":     {"type": "string"},
                        "description":        {"type": "string"},
                    },
                    "required": ["name", "type", "system_prompt", "tools", "behavioral_profile"],
                },
            },
            {
                "name": "register_agent",
                "description": "Register a new agent spec in the platform registry (agents.json + SQLite).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "spec": {"type": "object", "description": "Agent spec as returned by create_agent_spec"},
                    },
                    "required": ["spec"],
                },
            },
            {
                "name": "generate_agent_file",
                "description": "Generate a Python agent class file from an agent spec.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "spec":     {"type": "object"},
                        "filename": {"type": "string", "description": "Output filename (e.g. 'my_agent.py')"},
                    },
                    "required": ["spec"],
                },
            },
            {
                "name": "list_registered_agents",
                "description": "List all currently registered agents in the platform.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "description": "Filter by agent type (optional)"},
                    },
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "create_agent_spec":
            return self._create_spec(tool_input)

        if tool_name == "register_agent":
            return self._register_agent(tool_input["spec"])

        if tool_name == "generate_agent_file":
            return self._generate_file(tool_input["spec"], tool_input.get("filename"))

        if tool_name == "list_registered_agents":
            registry = get_registry()
            agents   = registry.list_agents(agent_type=tool_input.get("type"))
            return json.dumps({"agents": [
                {"id": a["id"], "name": a["name"], "type": a["type"], "profile": a["behavioral_profile"]}
                for a in agents
            ]})

        return super().execute_tool(tool_name, tool_input)

    # ── Tool implementations ───────────────────────────────────────────────

    def _create_spec(self, params: dict) -> str:
        spec = {
            "id":                 str(uuid.uuid4()),
            "name":               params["name"],
            "type":               params["type"],
            "model":              "claude-sonnet-4-6",
            "system_prompt":      params["system_prompt"],
            "tools":              params.get("tools", []),
            "knowledge_base":     params.get("knowledge_base", f"kb_{params['type']}"),
            "behavioral_profile": params.get("behavioral_profile", "Architect"),
            "created_by":         "meta-agent",
            "created_at":         datetime.now(timezone.utc).date().isoformat(),
            "enabled":            True,
            "metadata": {
                "description": params.get("description", ""),
                "version":     "1.0.0",
            },
        }
        return json.dumps({"spec": spec, "created": True})

    def _register_agent(self, spec: dict) -> str:
        try:
            registry = get_registry()
            saved    = registry.register_agent(spec)
            return json.dumps({
                "registered": True,
                "id":         saved["id"],
                "name":       saved["name"],
                "type":       saved["type"],
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _generate_file(self, spec: dict, filename: str = None) -> str:
        try:
            name        = spec["name"]
            class_name  = "".join(w.capitalize() for w in name.replace("-", " ").split())
            if not class_name.endswith("Agent"):
                class_name += "Agent"

            if not filename:
                filename = name.lower().replace(" ", "_") + ".py"

            output_path = AGENTS_DIR / filename

            # Build extra_tools list as Python repr
            tool_names = [t for t in spec.get("tools", []) if t not in ("remember", "recall")]
            extra_tools_repr = "[]"
            if tool_names:
                extra_tools_repr = "[\n            # Add tool schemas here for: " + ", ".join(tool_names) + "\n        ]"

            code = AGENT_CLASS_TEMPLATE.format(
                docstring         = f"{name} — generated by Meta Agent on {spec.get('created_at', 'unknown')}",
                class_name        = class_name,
                name              = name,
                agent_type        = spec.get("type", "custom"),
                behavioral_profile= spec.get("behavioral_profile", "Architect"),
                system_prompt     = spec.get("system_prompt", "").replace('"""', "'''"),
                extra_tools       = extra_tools_repr,
            )

            output_path.write_text(code)
            return json.dumps({
                "generated": True,
                "path":      str(output_path),
                "class_name": class_name,
                "filename":  filename,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec",  type=str, required=True, help="Natural language agent spec")
    parser.add_argument("--list",  action="store_true", help="List existing agents")
    args = parser.parse_args()

    agent = MetaAgent(name="Meta Agent")

    if args.list:
        registry = get_registry()
        agents   = registry.list_agents()
        for a in agents:
            print(f"  [{a['type']:15}] {a['name']}")
    else:
        result = agent.run(f"Create a new agent from this specification: {args.spec}")
        print(result["output"])
