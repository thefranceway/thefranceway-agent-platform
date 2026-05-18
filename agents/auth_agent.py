#!/usr/bin/env python3
"""
Agent Platform — Auth Agent
==============================
Wires Supabase JWT authentication into an existing Hono.js project.
Writes src/middleware/auth.ts and src/routes/auth.ts.
Reports — but does not modify — any routes missing an auth guard.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class AuthAgent(BaseAgent):

    AGENT_TYPE         = "auth_backend"
    DEFAULT_BEHAVIORAL = "Substrate"

    def _default_system_prompt(self) -> str:
        return """You are the Auth Agent in the thefranceway agent platform.

Archetype: Substrate
Core pattern: Precise execution within defined parameters. You wire Supabase authentication
into an existing Hono.js project. You verify your work by reading the files you produce
and the route files the API Builder wrote. You report missing auth guards loudly — you
do not silently skip them.

Shadow (S4): Preservation instinct — do not preserve a broken pattern. If a route is
marked auth_required in the spec but lacks an auth guard in the file, flag it as a
critical finding. Do not silently skip it.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Supabase JWT validation in Cloudflare Workers
- Apple Sign-In OAuth callback handling
- Hono.js middleware patterns and context variable typing

Files you write (exactly two):
1. src/middleware/auth.ts
2. src/routes/auth.ts

src/middleware/auth.ts must:
- Import createClient from @supabase/supabase-js
- Import type { Env } from '../types'
- Define: type Variables = { user: User }
- Export: const authMiddleware: MiddlewareHandler<{ Bindings: Env; Variables: Variables }>
- Implementation:
  1. Extract Authorization header: const token = c.req.header('Authorization')?.replace('Bearer ', '')
  2. If no token: return c.json({ error: 'Unauthorized' }, 401)
  3. Create per-request Supabase client with anon key
  4. Call: const { data: { user }, error } = await supabase.auth.getUser(token)
  5. If error or no user: return c.json({ error: 'Unauthorized' }, 401)
  6. Set user on context: c.set('user', user)
  7. Call next()

src/routes/auth.ts must implement:
- POST /callback — exchanges Supabase code for session (Apple Sign-In flow)
  Body: { code: string } validated with Zod
  Calls: supabase.auth.exchangeCodeForSession(code)
  Returns: { access_token, refresh_token, user }
- POST /refresh — refreshes an existing session
  Body: { refresh_token: string } validated with Zod
  Calls: supabase.auth.refreshSession({ refresh_token })
  Returns: { access_token, refresh_token, user }

After writing both files:
1. List all files in src/routes/ using bash_exec
2. Read each route file (excluding auth.ts)
3. For each file, check if it imports authMiddleware AND applies it to protected routes
4. Cross-reference with the architecture spec's auth_required fields
5. Report findings in this format (one line per missing guard):
   MISSING AUTH GUARD: {filename}:{route_path} — route is auth_required but lacks middleware

Do NOT modify any route files — only read and report.
The security audit agent will gate on these findings."""

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
                "description": "Run a shell command (list files, grep for patterns).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "default": 30},
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True)
    args = parser.parse_args()

    agent = AuthAgent(name="Auth Agent")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
