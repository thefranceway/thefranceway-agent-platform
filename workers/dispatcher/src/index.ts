/**
 * Agent Platform — Dispatcher Worker
 * ====================================
 * Public HTTP entry point — proxies to api_server.py (the real, canonical
 * task store) instead of maintaining its own D1 copy.
 *
 * D1 used to be a second, disconnected task queue from core/task_queue.py's
 * SQLite store — a task submitted here never showed up in /tasks on the
 * Python side, and vice versa. The orchestrator that actually executes tasks
 * has to run locally (needs the Anthropic key, local agent code, local
 * memory), so it can never be purely edge-native — meaning SQLite was always
 * the only system that could be the real source of truth. This Worker is now
 * a thin proxy so there's exactly one queue, not two.
 *
 * Endpoints (same paths as before; response shapes now come straight from
 * api_server.py rather than a hand-rolled D1 shape):
 *   POST /task          — Submit a task
 *   GET  /task/:id      — Get task status
 *   GET  /tasks          — List recent tasks
 *   GET  /agents        — List registered agents
 *   GET  /status        — Platform health
 *
 * Deploy: wrangler deploy (from workers/dispatcher/)
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
};

type Env = {
  BACKEND_URL: string;       // e.g. https://api.thefranceway.com
  PLATFORM_API_KEY: string;  // must match the Python server's PLATFORM_API_KEY
  PLATFORM_VERSION: string;
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
}

async function proxy(
  env: Env,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const backendUrl = env.BACKEND_URL || 'http://localhost:8788';
  const resp = await fetch(`${backendUrl}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${env.PLATFORM_API_KEY ?? ''}`,
      ...(init.headers ?? {}),
    },
    signal: AbortSignal.timeout(30_000),
  });
  const body = await resp.text();
  return new Response(body, {
    status:  resp.status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
}

// ── Route handlers ────────────────────────────────────────────────────────────

async function handleSubmitTask(request: Request, env: Env): Promise<Response> {
  let body: { description?: string; agent_type?: string; priority?: number };
  try {
    body = await request.json();
  } catch {
    return json({ error: 'Invalid JSON body' }, 400);
  }
  if (!body.description) {
    return json({ error: 'description is required' }, 400);
  }
  // async_mode: true preserves the original "submit, get task_id back
  // immediately, poll for the result" contract this endpoint always had.
  return proxy(env, '/task', {
    method: 'POST',
    body: JSON.stringify({
      description: body.description,
      agent_type:  body.agent_type,
      priority:    body.priority ?? 5,
      async_mode:  true,
    }),
  });
}

// ── Main handler ──────────────────────────────────────────────────────────────

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url    = new URL(request.url);
    const method = request.method;

    if (method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    // POST /task — submit task
    if (method === 'POST' && url.pathname === '/task') {
      return handleSubmitTask(request, env);
    }

    // GET /task/:id — get task status
    const taskMatch = url.pathname.match(/^\/task\/([a-f0-9-]{36})$/);
    if (method === 'GET' && taskMatch) {
      return proxy(env, `/task/${taskMatch[1]}`);
    }

    // GET /tasks — list tasks
    if (method === 'GET' && url.pathname === '/tasks') {
      return proxy(env, `/tasks${url.search}`);
    }

    // GET /agents — list agents
    if (method === 'GET' && url.pathname === '/agents') {
      return proxy(env, `/agents${url.search}`);
    }

    // GET /status or GET /
    if (method === 'GET' && (url.pathname === '/status' || url.pathname === '/')) {
      return proxy(env, '/status');
    }

    return json({
      error:     'Not found',
      endpoints: {
        'POST /task':      'Submit a task { description, agent_type?, priority? }',
        'GET /task/:id':   'Get task status',
        'GET /tasks':      'List tasks (?status=pending|running|done|failed)',
        'GET /agents':     'List registered agents',
        'GET /status':     'Platform health',
      },
    }, 404);
  },
};
