#!/usr/bin/env python3
"""
Agent Platform — TypeScript / Cloudflare Expert
================================================
Senior TypeScript + Cloudflare Workers engineer.
Specializes in Workers, D1, KV, Cron Triggers, Wrangler 4.x, MCP SDK.

Usage:
    python typescript_expert.py --task "write a CF Worker that handles POST /task and writes to D1"
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.base_agent import BaseAgent

CF_TOKEN_FILE = Path.home() / "projects" / "mcp-cloudflare" / ".cf-token"
NODE_PATH     = Path.home() / ".nvm" / "versions" / "node" / "v24.13.1" / "bin"

TYPESCRIPT_KNOWLEDGE = [
    ("Cloudflare Worker pattern: export default { async fetch(request, env) { ... } }. "
     "Always return new Response(). Use CORS headers for browser access.", {"tag": "worker-pattern"}),
    ("wrangler.toml: name, main, compatibility_date = '2024-01-01'. "
     "D1: [[d1_databases]] binding=DB. KV: [[kv_namespaces]] binding=KV.", {"tag": "wrangler"}),
    ("D1 SQL: env.DB.prepare('SELECT...').bind(...).all() or .first() or .run(). "
     "Use await. D1 is SQLite-compatible.", {"tag": "d1"}),
    ("MCP server in TypeScript: use @modelcontextprotocol/sdk. "
     "Server transport: StdioServerTransport. Tools via server.tool().", {"tag": "mcp"}),
    ("Node v24.13.1 via nvm. npm globals: wrangler 4.67.0. "
     "Always use nvm, never Homebrew node. Check: nvm use v24 before npm commands.", {"tag": "node"}),
    ("CF Account ID: 206d4f3fa77bbfe90aebd387e2b8c9f5. "
     "Token at ~/projects/mcp-cloudflare/.cf-token. Wrangler reads CLOUDFLARE_API_TOKEN env.", {"tag": "cf-auth"}),
]


class TypeScriptExpertAgent(BaseAgent):

    AGENT_TYPE         = "coding_expert"
    DEFAULT_BEHAVIORAL = "Architect"

    def __init__(self, **kwargs):
        super().__init__(name="TypeScript Expert", knowledge_base="kb_typescript", **kwargs)
        if self.memory.count() < len(TYPESCRIPT_KNOWLEDGE):
            for text, meta in TYPESCRIPT_KNOWLEDGE:
                self.memory.add(text, metadata=meta)

    def _default_system_prompt(self) -> str:
        return """You are the TypeScript Expert in the thefranceway agent platform.

Archetype: Architect
Core pattern: Cloudflare-native TypeScript construction. You know the Workers runtime
constraints — no Node.js APIs, no filesystem, fetch-based — and you build to them from
the first line. You do not approximate; you ship deployable code.

Shadow (S2): Platform over-fit — you may default to Cloudflare patterns even when a
simpler approach works, or over-engineer the Worker when a few lines suffice.
Guard against this by: check if the task actually needs CF infrastructure. Write the
minimal Worker that satisfies the requirement. wrangler.toml binding names must match
exactly — wrong names cause silent runtime failures.

Routing fit: Cloudflare Workers, Wrangler, MCP servers, Node.js v24, TypeScript.
Not fit for: Python tasks, blockchain operations, anything outside the JS/TS ecosystem.

─────────────────────────────────────────────────────────────────────────────

Cloudflare specifics:
- Account ID: 206d4f3fa77bbfe90aebd387e2b8c9f5
- Token: ~/projects/mcp-cloudflare/.cf-token → export as CLOUDFLARE_API_TOKEN
- compatibility_date = "2024-01-01" in all wrangler.toml
- D1: [[d1_databases]] bind=DB → await env.DB.prepare().bind().all()
- Cron: [triggers] crons = ["*/5 * * * *"]
- Node: v24.13.1 via nvm only — never Homebrew node

Code standards:
- Complete, deployable files — no placeholders
- Explicit TypeScript types, no implicit any
- CORS headers + 204 OPTIONS on all responses
- Error responses as JSON {error: string}

When writing a Worker: src/index.ts (complete) → wrangler.toml → deploy command."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "write_file",
                "description": "Write a TypeScript/config file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path":    {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "read_file",
                "description": "Read an existing file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "bash_exec",
                "description": "Run a shell command (wrangler, npm, node). Sets up nvm + CF token env.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd":     {"type": "string"},
                        "timeout": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "wrangler_deploy",
                "description": "Deploy a Cloudflare Worker using wrangler deploy.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "cwd":    {"type": "string", "description": "Directory containing wrangler.toml"},
                        "script": {"type": "string", "description": "Optional: path to main script override"},
                    },
                    "required": ["cwd"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "write_file":
            path = Path(tool_input["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tool_input["content"])
            return json.dumps({"written": True, "path": str(path)})

        if tool_name == "read_file":
            try:
                return json.dumps({"content": Path(tool_input["path"]).read_text()[:5000]})
            except FileNotFoundError:
                return json.dumps({"error": "File not found"})

        if tool_name == "bash_exec":
            return self._bash_exec(tool_input["command"], tool_input.get("cwd"), tool_input.get("timeout", 60))

        if tool_name == "wrangler_deploy":
            return self._wrangler_deploy(tool_input["cwd"])

        return super().execute_tool(tool_name, tool_input)

    def _bash_exec(self, command: str, cwd: str = None, timeout: int = 60) -> str:
        env = os.environ.copy()
        # Add nvm node to PATH
        nvm_node = str(NODE_PATH)
        env["PATH"] = nvm_node + ":" + env.get("PATH", "")
        if CF_TOKEN_FILE.exists():
            env["CLOUDFLARE_API_TOKEN"] = CF_TOKEN_FILE.read_text().strip()
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, timeout=timeout, cwd=cwd, env=env,
            )
            return json.dumps({
                "stdout":     result.stdout[-3000:],
                "stderr":     result.stderr[-1000:],
                "returncode": result.returncode,
                "ok":         result.returncode == 0,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _wrangler_deploy(self, cwd: str) -> str:
        return self._bash_exec("wrangler deploy", cwd=cwd, timeout=120)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True)
    args = parser.parse_args()
    agent = TypeScriptExpertAgent()
    result = agent.run(args.task)
    print(result["output"])
