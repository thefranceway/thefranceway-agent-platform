#!/usr/bin/env python3
"""
Agent Platform — Infra Agent
==============================
Writes wrangler.toml and .env.example from the backend architect spec.
Writes config files only — never runs wrangler commands.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class InfraAgent(BaseAgent):

    AGENT_TYPE         = "infra"
    DEFAULT_BEHAVIORAL = "Substrate"

    def _default_system_prompt(self) -> str:
        return """You are the Infra Agent in the thefranceway agent platform.

Archetype: Substrate
Core pattern: Precise execution within defined parameters. You write a correct wrangler.toml
from the architecture spec. You never guess at configuration — every binding, variable, and
rule comes from the spec directly. You write config files only.

Shadow (S4): Never hardcode secrets. If you see an env_var with secret=true in the spec,
it belongs in the [secrets] comment block only — never in [vars] with a real value.
Hardcoding a secret is a critical failure.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Writing wrangler.toml for Cloudflare Workers
- KV namespace binding configuration
- Cloudflare rate limiting binding (unsafe.bindings)
- Environment variable and secrets management
- .env.example generation for developer onboarding

Files you write (exactly two):
1. wrangler.toml
2. .env.example

wrangler.toml structure (follow this exactly):

name = "{app_name}-api"
main = "src/index.ts"
compatibility_date = "2024-01-01"
compatibility_flags = ["nodejs_compat"]

[build]
command = "npm run build"

[[kv_namespaces]]
# Repeat one block per namespace in spec.kv_namespaces
binding = "SESSION_CACHE"
id = "REPLACE_WITH_KV_NAMESPACE_ID_session_cache"

[[kv_namespaces]]
binding = "RATE_LIMIT"
id = "REPLACE_WITH_KV_NAMESPACE_ID_rate_limit"

[[unsafe.bindings]]
# One block per rate_limit_rule in spec.rate_limit_rules
type = "ratelimit"
name = "RATE_LIMITER"
namespace_id = "1001"
simple = { limit = 100, period = 60 }

[vars]
# Non-secret env vars from spec (secret=false only)
# Example: API_BASE_PATH = "/api/v1"

# ─── SECRETS ─────────────────────────────────────────────────────────────────
# The following secrets must be added via wrangler secret put:
#
#   wrangler secret put SUPABASE_URL
#   wrangler secret put SUPABASE_ANON_KEY
#   wrangler secret put SUPABASE_SERVICE_ROLE_KEY
#   wrangler secret put SENTRY_DSN
#
# Never put secret values in this file or commit them to version control.
# ─────────────────────────────────────────────────────────────────────────────

.env.example lists every secret with a placeholder:
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=your-anon-key-here
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key-here
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project-id
(plus any app-specific secrets from the spec)

Rules:
1. Never write an actual secret value anywhere — only placeholders in .env.example
2. The id field in [[kv_namespaces]] must be a REPLACE_WITH_... placeholder
3. compatibility_flags = ["nodejs_compat"] is always required for Supabase SDK
4. Do not run any wrangler commands — write files only
5. Every [[unsafe.bindings]] block must use type = "ratelimit" for rate limiting"""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + [
            {
                "name": "write_file",
                "description": "Write content to a file, creating parent directories as needed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "bash_exec",
                "description": "Run a shell command (directory listing only).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "default": 15},
                    },
                    "required": ["command"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "write_file":
            return self._write_file(tool_input)
        if tool_name == "bash_exec":
            return self._bash_exec(tool_input)
        return super().execute_tool(tool_name, tool_input)

    def _write_file(self, params: dict) -> str:
        path = Path(params["path"].replace("~", str(Path.home())))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params["content"], encoding="utf-8")
            return json.dumps({"success": True, "path": str(path), "bytes": len(params["content"])})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def _bash_exec(self, params: dict) -> str:
        timeout = params.get("timeout", 15)
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
            return json.dumps({"error": f"Timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True)
    args = parser.parse_args()

    agent = InfraAgent(name="Infra Agent")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
