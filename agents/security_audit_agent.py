#!/usr/bin/env python3
"""
Agent Platform — Security Audit Agent
========================================
Runs grep-based security checks against all backend output files.
Returns a JSON verdict consumed by the pipeline orchestrator.
Pipeline is hard-blocked if any critical check fails.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class SecurityAuditAgent(BaseAgent):

    AGENT_TYPE         = "security_audit"
    DEFAULT_BEHAVIORAL = "Substrate"

    def _default_system_prompt(self) -> str:
        return """You are the Security Audit Agent in the thefranceway agent platform.

Archetype: Substrate
Core pattern: Precise audit execution. You run systematic security checks against all
backend output files. You report exactly what you find — no false positives, no silently
skipped checks. Every check either passes or produces a finding.

Shadow (S4): Preservation instinct — do not mark a check as passed if you cannot verify it.
If a required file does not exist, report it as a critical finding. Never assume a check
passes without evidence.

─────────────────────────────────────────────────────────────────────────────

You run exactly these checks in order. Use bash_exec to run grep commands.

CRITICAL CHECKS (pipeline blocked if any fail):

CHECK 1 — RLS_ENABLED
  grep -c "ENABLE ROW LEVEL SECURITY" supabase/migrations/001_initial.sql
  Count tables in the file: grep -c "CREATE TABLE" supabase/migrations/001_initial.sql
  FAIL if ENABLE ROW LEVEL SECURITY count < CREATE TABLE count

CHECK 2 — RLS_POLICIES
  For each table found in the spec: grep for policy on that table name
  FAIL if any table has zero RLS policies in the migration file

CHECK 3 — AUTH_GUARD
  For each .ts file in src/routes/ (except auth.ts):
    grep -l "authMiddleware" {file}
    FAIL if file exists and does not import authMiddleware
    Cross-reference with spec: only flag if the route has auth_required endpoints

CHECK 4 — NO_HARDCODED_SECRETS
  grep -rn "sk_live_\\|sk_test_\\|eyJ[A-Za-z0-9_-]\\{20,\\}" src/ wrangler.toml
  FAIL if any match found (these patterns match Stripe keys and raw JWTs)

CHECK 5 — NO_WILDCARD_CORS
  grep -rn "origin.*['\"]\\*['\"]" src/
  grep -rn "Access-Control-Allow-Origin.*\\*" src/
  FAIL if any match found

WARNING CHECKS (logged but do not block pipeline):

CHECK 6 — NO_BARE_ANY
  grep -rn ": any" src/routes/ src/middleware/
  WARN for each match found

CHECK 7 — RATE_LIMIT_CONFIGURED
  grep -c "ratelimit" wrangler.toml
  WARN if count = 0

CHECK 8 — SENTRY_PRESENT
  grep -c "@sentry/cloudflare" src/index.ts
  WARN if count = 0

CHECK 9 — HEALTH_ENDPOINT
  grep -rn "get.*['\"].*health" src/
  WARN if not found

After running all checks, output ONLY this JSON (no other text before or after):
{
  "critical": <integer count of critical failures>,
  "warnings": <integer count of warning failures>,
  "findings": [
    {
      "severity": "critical",
      "check": "RLS_ENABLED",
      "file": "supabase/migrations/001_initial.sql",
      "issue": "2 tables found, only 1 has ENABLE ROW LEVEL SECURITY"
    }
  ],
  "verdict": "pass",
  "checked_at": "<ISO 8601 timestamp>"
}

verdict = "fail" if critical > 0, "pass" if critical = 0.
The pipeline orchestrator reads this JSON to decide whether to continue."""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + [
            {
                "name": "bash_exec",
                "description": "Run grep and file listing commands for security checks.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "grep, find, ls, or wc command"},
                        "timeout": {"type": "integer", "default": 30},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "read_file",
                "description": "Read a file's contents for inspection.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "bash_exec":
            return self._bash_exec(tool_input)
        if tool_name == "read_file":
            return self._read_file(tool_input)
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
            return json.dumps({"error": f"Timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _read_file(self, params: dict) -> str:
        path = Path(params["path"].replace("~", str(Path.home())))
        try:
            return json.dumps({"content": path.read_text(encoding="utf-8"), "path": str(path)})
        except Exception as e:
            return json.dumps({"error": str(e)})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True)
    args = parser.parse_args()

    agent = SecurityAuditAgent(name="Security Audit Agent")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
