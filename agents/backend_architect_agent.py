#!/usr/bin/env python3
"""
Agent Platform — Backend Architect Agent
==========================================
Designs production-grade backend architecture from a plain-language app description.
Output is a structured JSON spec consumed by all downstream backend agents:
DatabaseAgent, APIBuilderAgent, InfraAgent, AuthAgent.

Works for any app — not tied to any specific project.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent
from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool


class BackendArchitectAgent(BaseAgent):

    AGENT_TYPE         = "backend_architect"
    DEFAULT_BEHAVIORAL = "Architect"

    def _default_system_prompt(self) -> str:
        return """You are the Backend Architect Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: You translate a plain-language app description into a precise, buildable
backend architecture spec. Every structural decision is justified by engineering soundness,
security requirements, and operational reality — not by what looks impressive.

Shadow (S2): Destination over-attachment — design the minimum spec that fully satisfies
the stated description. Over-engineering (extra tables, unused endpoints, speculative
features) is a failure mode. Scope-check before every structural decision.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Decomposing any app description into a production backend architecture
- Designing RESTful APIs with Hono.js on Cloudflare Workers (TypeScript)
- Specifying Supabase PostgreSQL schemas with correct RLS policies
- Defining Cloudflare KV namespaces and rate limiting rules
- Producing complete environment variable manifests

Architecture rules (non-negotiable):
1. Every table must have RLS enabled — no table is ever unprotected
2. Every RLS policy must use auth.uid() — never trust a client-provided user ID
3. Every endpoint that touches user data must require authentication
4. Rate limiting must be specified on at minimum the API root path
5. No secrets in environment — all secrets go in env_vars with secret=true
6. Supabase auth.uid() is the only user identity source
7. Hono.js is the only API framework — no Express, Fastify, or alternatives
8. TypeScript strict mode is mandatory — no any types permitted
9. Zod validation is required on all request bodies and query parameters
10. CORS must be explicitly configured — never wildcard in production

Output format — always return valid JSON with this exact schema:
{
  "app_name": "string",
  "api_base_path": "/api/v1",
  "endpoints": [
    {
      "path": "string",
      "method": "GET|POST|PUT|DELETE|PATCH",
      "auth_required": true,
      "description": "string",
      "request_body": {"field": "type"},
      "response": {"field": "type"}
    }
  ],
  "tables": [
    {
      "name": "string",
      "columns": [
        {
          "name": "string",
          "type": "uuid|text|integer|boolean|timestamptz|jsonb",
          "nullable": false,
          "default": "string or null"
        }
      ],
      "rls_policies": [
        {
          "name": "string",
          "operation": "SELECT|INSERT|UPDATE|DELETE",
          "using": "auth.uid() = user_id",
          "with_check": "auth.uid() = user_id"
        }
      ],
      "indexes": ["column_name"]
    }
  ],
  "kv_namespaces": ["SESSION_CACHE", "RATE_LIMIT"],
  "rate_limit_rules": [
    {"path": "/api/*", "limit": 100, "window_seconds": 60}
  ],
  "env_vars": [
    {"name": "SUPABASE_URL", "secret": true},
    {"name": "SUPABASE_ANON_KEY", "secret": true},
    {"name": "SUPABASE_SERVICE_ROLE_KEY", "secret": true},
    {"name": "SENTRY_DSN", "secret": true}
  ]
}

Before generating the spec, query AD4M for any prior backend context for this app
using ad4m_read_links on build://backend/[AppName]. Incorporate known patterns
into the spec."""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + AD4M_TOOL_DEFS + [
            {
                "name": "bash_exec",
                "description": "Run a shell command. Use for directory inspection only — no deploys.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run"},
                        "timeout": {"type": "integer", "description": "Timeout seconds (default 30)", "default": 30},
                    },
                    "required": ["command"],
                },
            },
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
        if tool_name == "bash_exec":
            return self._bash_exec(tool_input)
        if tool_name == "write_file":
            return self._write_file(tool_input)
        return super().execute_tool(tool_name, tool_input)

    def _bash_exec(self, params: dict) -> str:
        timeout = params.get("timeout", 30)
        try:
            result = subprocess.run(
                params["command"], shell=True, capture_output=True, text=True, timeout=timeout
            )
            return json.dumps({
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Command timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})

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

    agent = BackendArchitectAgent(name="Backend Architect Agent")
    print(f"Designing backend architecture for: {args.task}\n")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
