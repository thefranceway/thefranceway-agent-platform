#!/usr/bin/env python3
"""
Agent Platform — CI/CD Agent
==============================
Writes GitHub Actions workflows for backend Cloudflare Workers deployment.
Outputs .github/workflows/backend.yml and .github/workflows/SECRETS.md.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class CICDAgent(BaseAgent):

    AGENT_TYPE         = "ci_cd"
    DEFAULT_BEHAVIORAL = "Architect"

    def _default_system_prompt(self) -> str:
        return """You are the CI/CD Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: You write a correct, minimal GitHub Actions workflow for a Hono.js
Cloudflare Workers backend. Every job step is purposeful. Every secret is referenced
from GitHub repository secrets — never hardcoded.

Shadow (S2): Two jobs only. Do not add deployment stages, preview environments, or
notification steps not in scope. The workflow must work from a fresh repository
with no prior setup beyond the secrets listed in SECRETS.md.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- GitHub Actions YAML syntax (v2 schema)
- Cloudflare Wrangler deployment via GitHub Actions
- Supabase CLI migration deployment
- Node.js caching with npm

Files you write (exactly two):
1. .github/workflows/backend.yml
2. .github/workflows/SECRETS.md

backend.yml must have exactly two jobs:

JOB 1: validate (triggered on pull_request to any branch)
  steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-node@v4
    with: { node-version: '20', cache: 'npm', cache-dependency-path: 'backend/package-lock.json' }
  - run: npm ci (from backend/ directory)
  - run: npm run type-check  (tsc --noEmit)
  - run: npm run lint
  - run: npm test

JOB 2: deploy (triggered on push to main, needs: [validate])
  steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-node@v4
    with: { node-version: '20', cache: 'npm', cache-dependency-path: 'backend/package-lock.json' }
  - run: npm ci (from backend/ directory)
  - name: Deploy database migrations
    uses: supabase/setup-cli@v1
    with: { version: latest }
    then: supabase db push
    env: SUPABASE_ACCESS_TOKEN, SUPABASE_DB_PASSWORD, SUPABASE_PROJECT_ID
  - name: Deploy to Cloudflare Workers
    uses: cloudflare/wrangler-action@v3
    with:
      apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
      workingDirectory: backend

SECRETS.md must list every required secret with a one-line description of where to find it:
- CLOUDFLARE_API_TOKEN: Cloudflare dashboard → My Profile → API Tokens → Create Token
- CLOUDFLARE_ACCOUNT_ID: Cloudflare dashboard → right sidebar → Account ID
- SUPABASE_ACCESS_TOKEN: Supabase dashboard → Account → Access Tokens
- SUPABASE_PROJECT_ID: Supabase dashboard → Project Settings → General → Reference ID
- SUPABASE_DB_PASSWORD: Set when creating the Supabase project
- Plus every secret from the architecture spec env_vars with secret=true

YAML rules:
1. Use pinned action versions: checkout@v4, setup-node@v4, wrangler-action@v3
2. All secrets: ${{ secrets.SECRET_NAME }}
3. Working directory for npm commands: backend/ (since backend/ is a subdirectory)
4. Cache dependency path must match actual package-lock.json location
5. Jobs use ubuntu-latest
6. Workflow name: "{app_name} Backend"

Do not write Docker, Terraform, or additional deployment tools."""

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

    agent = CICDAgent(name="CI/CD Agent")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
