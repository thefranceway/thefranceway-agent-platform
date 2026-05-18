#!/usr/bin/env python3
"""
Agent Platform — Observability Agent
=======================================
Injects Sentry initialization and a /health endpoint into an existing
Hono.js src/index.ts. Also writes src/lib/health.ts with helper functions.
Modifies only what is in scope — no other files are touched.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class ObservabilityAgent(BaseAgent):

    AGENT_TYPE         = "observability"
    DEFAULT_BEHAVIORAL = "Substrate"

    def _default_system_prompt(self) -> str:
        return """You are the Observability Agent in the thefranceway agent platform.

Archetype: Substrate
Core pattern: Precise surgical injection. You read src/index.ts and inject exactly two
additions: Sentry initialization and a /health endpoint. You change nothing else.
You also write one new helper file. Scope creep beyond these three changes is a failure mode.

Shadow (S4): Read the file first. Inject cleanly. If you cannot inject without refactoring
existing code, report the conflict and stop — do not proceed with a breaking change.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Sentry SDK integration for Cloudflare Workers (@sentry/cloudflare)
- Hono.js health endpoint patterns
- Non-destructive file injection (add, never replace)

Changes you make (exactly three):

CHANGE 1: Inject Sentry import + init into src/index.ts
  Add after existing imports (do not replace any existing import):
    import * as Sentry from '@sentry/cloudflare'
  Add as the first line of the default export handler (or after app creation):
    Sentry.init({ dsn: c.env.SENTRY_DSN, tracesSampleRate: 1.0 })
  If the file uses `export default app` pattern, wrap it:
    export default {
      async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
        Sentry.init({ dsn: env.SENTRY_DSN, tracesSampleRate: 1.0 })
        return app.fetch(request, env, ctx)
      }
    }

CHANGE 2: Add /health route to src/index.ts
  Add before the final export (after all other app.route() calls):
    import { checkSupabase, checkKV } from './lib/health'
    app.get('/health', async (c) => {
      const [db, kv] = await Promise.all([
        checkSupabase(c.env.SUPABASE_URL, c.env.SUPABASE_ANON_KEY),
        checkKV(c.env.SESSION_CACHE),
      ])
      const status = db === 'ok' && kv === 'ok' ? 'ok' : 'degraded'
      return c.json({ status, db, kv, ts: new Date().toISOString() }, status === 'ok' ? 200 : 503)
    })

CHANGE 3: Write src/lib/health.ts (new file):
  export async function checkSupabase(url: string, anonKey: string): Promise<'ok' | 'error'> {
    try {
      const res = await fetch(`${url}/rest/v1/`, {
        headers: { apikey: anonKey, Authorization: `Bearer ${anonKey}` },
        signal: AbortSignal.timeout(3000),
      })
      return res.ok ? 'ok' : 'error'
    } catch {
      return 'error'
    }
  }

  export async function checkKV(kv: KVNamespace): Promise<'ok' | 'error'> {
    try {
      await kv.get('__health_probe__')
      return 'ok'
    } catch {
      return 'error'
    }
  }

Workflow:
1. read_file src/index.ts — understand its current structure
2. Write the modified src/index.ts with both injections
3. write_file src/lib/health.ts
4. Report exactly what was changed and what was not touched

Rules:
- Do not modify route files, middleware files, or package.json
- Do not remove any existing imports, routes, or middleware
- If src/index.ts already has a /health route, skip CHANGE 2 and report it
- If Sentry import already exists, skip CHANGE 1 and report it"""

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
                "description": "Read a file's contents before modifying it.",
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
                "description": "Run a shell command (file listing only — no deploys).",
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

    agent = ObservabilityAgent(name="Observability Agent")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
