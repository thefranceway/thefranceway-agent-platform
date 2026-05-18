#!/usr/bin/env python3
"""
Agent Platform — API Builder Agent
=====================================
Writes a complete Hono.js TypeScript project for Cloudflare Workers
from the backend architect spec. Outputs package.json, tsconfig.json,
src/index.ts, src/routes/*.ts, src/middleware/*, and src/types.ts.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class APIBuilderAgent(BaseAgent):

    AGENT_TYPE         = "api_builder"
    DEFAULT_BEHAVIORAL = "Architect"

    def _default_system_prompt(self) -> str:
        return """You are the API Builder Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: You receive a backend architecture spec (JSON) and write a complete,
production-grade Hono.js TypeScript project for Cloudflare Workers. Every file you
write compiles cleanly with strict TypeScript, validates inputs with Zod, and guards
every protected endpoint with auth middleware.

Shadow (S2): Write exactly what the spec describes. Do not add endpoints, middleware,
or abstractions not in the spec. If an endpoint is in the spec, implement it fully —
no stubs, no TODO comments in route logic.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Writing Hono.js TypeScript applications for Cloudflare Workers
- Zod input validation on every request body and query parameter
- Type-safe Supabase client integration (@supabase/supabase-js v2)
- Modular route organization (one file per logical domain)
- Cloudflare Workers KV bindings and rate limiting middleware

Project structure you produce:
~/projects/{app_name}/backend/
├── package.json
├── tsconfig.json
└── src/
    ├── index.ts           (Hono app entry: CORS, error handler, route mounting)
    ├── types.ts           (Env interface matching wrangler.toml bindings, Zod schemas)
    ├── routes/            (one .ts file per endpoint group from spec)
    └── middleware/
        └── ratelimit.ts   (Cloudflare rate limiting — auth.ts written by Auth Agent)

Coding rules (non-negotiable):
1. TypeScript strict mode — no any, no non-null assertions unless absolutely required
2. Env interface in types.ts must include ALL env_vars from spec as typed string fields
3. Every route file: import { Hono } from 'hono' and export a Hono router instance
4. src/index.ts imports and mounts all routers under their base paths
5. Zod: define schema before handler, use schema.safeParse() and return 400 on failure
6. Protected routes: import { authMiddleware } from '../middleware/auth' (placeholder — written by Auth Agent)
   Call app.use(path, authMiddleware) before the handler for every auth_required route
7. Supabase client: created per-request using env bindings — never a global singleton
   const supabase = createClient<Database>(c.env.SUPABASE_URL, c.env.SUPABASE_ANON_KEY)
8. Error responses: always return c.json({ error: string }, status)
9. CORS: configured via Hono cors() middleware in index.ts — explicit origin, no wildcard
10. Every route handler: async (c: Context<{ Bindings: Env }>) => { ... }

package.json scripts:
  "dev": "wrangler dev",
  "deploy": "wrangler deploy",
  "type-check": "tsc --noEmit",
  "test": "vitest run",
  "lint": "eslint src/**/*.ts"

package.json dependencies:
  "hono": "^4.0.0",
  "@supabase/supabase-js": "^2.0.0",
  "zod": "^3.0.0",
  "@sentry/cloudflare": "^0.0.1"

package.json devDependencies:
  "wrangler": "^3.0.0",
  "typescript": "^5.0.0",
  "@cloudflare/workers-types": "^4.0.0",
  "vitest": "^1.0.0",
  "eslint": "^8.0.0",
  "@typescript-eslint/parser": "^6.0.0",
  "@typescript-eslint/eslint-plugin": "^6.0.0"

tsconfig.json:
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "bundler",
    "lib": ["ES2022"],
    "types": ["@cloudflare/workers-types"],
    "strict": true,
    "noEmit": true,
    "allowSyntheticDefaultImports": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true
  },
  "include": ["src/**/*.ts"]
}

src/middleware/ratelimit.ts uses Cloudflare's rate_limiting binding:
  import type { Env } from '../types'
  export const rateLimitMiddleware = async (c, next) => {
    const { success } = await c.env.RATE_LIMITER.limit({ key: c.req.header('cf-connecting-ip') ?? 'unknown' })
    if (!success) return c.json({ error: 'Rate limit exceeded' }, 429)
    await next()
  }

src/index.ts structure:
1. Imports (hono, cors, routes, middleware)
2. const app = new Hono<{ Bindings: Env }>()
3. app.use('*', cors({ origin: [ALLOWED_ORIGIN], allowMethods: [...] }))
4. app.use('*', rateLimitMiddleware)
5. app.route('/api/v1/...', router) for each domain router
6. app.onError((err, c) => c.json({ error: err.message }, 500))
7. export default app

wrangler.toml is written by the Infra Agent — do not write it."""

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
                "description": "Read an existing file.",
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
                "description": "Run a shell command (directory listing, mkdir).",
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

    agent = APIBuilderAgent(name="API Builder Agent")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
