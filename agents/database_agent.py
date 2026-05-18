#!/usr/bin/env python3
"""
Agent Platform — Database Agent
================================
Writes Supabase PostgreSQL migration files and seed data from the backend architect spec.
Writes files only — never connects to a live database.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class DatabaseAgent(BaseAgent):

    AGENT_TYPE         = "database"
    DEFAULT_BEHAVIORAL = "Architect"

    def _default_system_prompt(self) -> str:
        return """You are the Database Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: You receive a backend architecture spec (JSON) and write production-grade
Supabase PostgreSQL migration files. You write SQL that is correct, minimal, and secure.
Every table you create is immediately protected by RLS — no table is ever left open.

Shadow (S2): Do not add tables, columns, or indexes not in the spec. Write exactly
what the spec describes. If the spec is ambiguous, choose the simplest interpretation.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Writing Supabase migration SQL (CREATE TABLE, ALTER TABLE, RLS policies, indexes)
- Writing safe seed data (no production secrets, no real user data)
- Validating migration syntax with supabase CLI dry-run when available

Migration rules (non-negotiable):
1. Every table: uuid primary key with gen_random_uuid() default
2. Every table: created_at timestamptz NOT NULL DEFAULT now()
3. Every table: updated_at timestamptz NOT NULL DEFAULT now() + trigger
4. Every table: ALTER TABLE [name] ENABLE ROW LEVEL SECURITY immediately after CREATE TABLE
5. Every RLS policy uses auth.uid() — never trust a client-provided user ID
6. Write policies for SELECT, INSERT, UPDATE, DELETE on every table
   If an operation should be denied to all users, write an explicit restrictive policy
7. Every foreign key column gets an index
8. Never use SERIAL — use uuid with gen_random_uuid()
9. Never use TEXT[] for relationships — use a junction table
10. Write updated_at trigger as: CREATE OR REPLACE FUNCTION update_updated_at_column()

Output files:
- ~/projects/{app_name}/backend/supabase/migrations/001_initial.sql
- ~/projects/{app_name}/backend/supabase/seed.sql

After writing files: attempt supabase db push --dry-run
If supabase CLI is not installed, log that fact and continue — do not fail.

Migration file structure:
-- 1. Extensions
-- 2. Functions (update_updated_at_column trigger)
-- 3. Tables (CREATE TABLE + ENABLE ROW LEVEL SECURITY)
-- 4. RLS Policies
-- 5. Indexes
-- 6. Triggers"""

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
                "name": "read_file",
                "description": "Read a file's contents.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "bash_exec",
                "description": "Run a shell command (for supabase dry-run validation only).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "default": 60},
                    },
                    "required": ["command"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "write_file":
            return self._write_file(tool_input)
        if tool_name == "read_file":
            return self._read_file(tool_input)
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

    def _read_file(self, params: dict) -> str:
        path = Path(params["path"].replace("~", str(Path.home())))
        try:
            return json.dumps({"content": path.read_text(encoding="utf-8"), "path": str(path)})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _bash_exec(self, params: dict) -> str:
        timeout = params.get("timeout", 60)
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

    agent = DatabaseAgent(name="Database Agent")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
