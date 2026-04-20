/**
 * Agent Platform — Dispatcher Worker
 * ====================================
 * Receives HTTP task requests → writes to D1 queue → returns task_id
 *
 * Endpoints:
 *   POST /task          — Submit a task
 *   GET  /task/:id      — Get task status
 *   GET  /tasks         — List recent tasks
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
  DB: D1Database;
  PLATFORM_VERSION: string;
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
}

function generateId(): string {
  return crypto.randomUUID();
}

// ── D1 helpers ────────────────────────────────────────────────────────────────

async function initSchema(db: D1Database): Promise<void> {
  await db.exec(`
    CREATE TABLE IF NOT EXISTS tasks (
      id          TEXT PRIMARY KEY,
      description TEXT NOT NULL,
      agent_type  TEXT,
      status      TEXT DEFAULT 'pending',
      priority    INTEGER DEFAULT 5,
      input       TEXT,
      output      TEXT,
      error       TEXT,
      created_at  TEXT,
      started_at  TEXT,
      ended_at    TEXT
    );

    CREATE TABLE IF NOT EXISTS agents (
      id                 TEXT PRIMARY KEY,
      name               TEXT NOT NULL,
      type               TEXT NOT NULL,
      model              TEXT DEFAULT 'claude-sonnet-4-6',
      behavioral_profile TEXT,
      enabled            INTEGER DEFAULT 1,
      created_at         TEXT
    );
  `);
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

  const taskId    = generateId();
  const createdAt = new Date().toISOString();

  try {
    await env.DB.prepare(`
      INSERT INTO tasks (id, description, agent_type, status, priority, created_at)
      VALUES (?, ?, ?, 'pending', ?, ?)
    `).bind(
      taskId,
      body.description,
      body.agent_type ?? null,
      body.priority ?? 5,
      createdAt,
    ).run();

    return json({
      task_id:    taskId,
      status:     'pending',
      agent_type: body.agent_type ?? 'auto-routed',
      created_at: createdAt,
      note:       'Task queued. Poll GET /task/' + taskId + ' for status.',
    }, 201);
  } catch (e) {
    // Schema might not exist yet — init and retry
    await initSchema(env.DB);
    await env.DB.prepare(`
      INSERT INTO tasks (id, description, agent_type, status, priority, created_at)
      VALUES (?, ?, ?, 'pending', ?, ?)
    `).bind(taskId, body.description, body.agent_type ?? null, body.priority ?? 5, createdAt).run();

    return json({ task_id: taskId, status: 'pending', created_at: createdAt }, 201);
  }
}

async function handleGetTask(taskId: string, env: Env): Promise<Response> {
  const task = await env.DB.prepare(
    'SELECT * FROM tasks WHERE id = ?'
  ).bind(taskId).first();

  if (!task) {
    return json({ error: 'Task not found', task_id: taskId }, 404);
  }

  // Parse JSON fields
  const output = task.output ? JSON.parse(task.output as string) : null;
  return json({ ...task, output });
}

async function handleListTasks(url: URL, env: Env): Promise<Response> {
  const status = url.searchParams.get('status');
  const limit  = Math.min(parseInt(url.searchParams.get('limit') ?? '20'), 100);

  let query  = 'SELECT id, description, agent_type, status, priority, created_at, ended_at FROM tasks';
  const params: unknown[] = [];

  if (status) {
    query += ' WHERE status = ?';
    params.push(status);
  }
  query += ' ORDER BY created_at DESC LIMIT ?';
  params.push(limit);

  const { results } = await env.DB.prepare(query).bind(...params).all();
  return json({ tasks: results, count: results.length });
}

async function handleListAgents(url: URL, env: Env): Promise<Response> {
  const type  = url.searchParams.get('type');
  let query   = 'SELECT * FROM agents WHERE enabled = 1';
  const params: unknown[] = [];

  if (type) {
    query += ' AND type = ?';
    params.push(type);
  }
  query += ' ORDER BY created_at DESC';

  const { results } = await env.DB.prepare(query).bind(...params).all();
  return json({ agents: results, count: results.length });
}

async function handleStatus(env: Env): Promise<Response> {
  const [pending, running, done, failed] = await Promise.all([
    env.DB.prepare("SELECT COUNT(*) as n FROM tasks WHERE status = 'pending'").first<{ n: number }>(),
    env.DB.prepare("SELECT COUNT(*) as n FROM tasks WHERE status = 'running'").first<{ n: number }>(),
    env.DB.prepare("SELECT COUNT(*) as n FROM tasks WHERE status = 'done'").first<{ n: number }>(),
    env.DB.prepare("SELECT COUNT(*) as n FROM tasks WHERE status = 'failed'").first<{ n: number }>(),
  ]).catch(() => [null, null, null, null]);

  return json({
    platform:  'agent-dispatcher',
    version:   env.PLATFORM_VERSION ?? '1.0.0',
    queue: {
      pending: pending?.n ?? 0,
      running: running?.n ?? 0,
      done:    done?.n    ?? 0,
      failed:  failed?.n  ?? 0,
    },
    timestamp: new Date().toISOString(),
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
      return handleGetTask(taskMatch[1], env);
    }

    // GET /tasks — list tasks
    if (method === 'GET' && url.pathname === '/tasks') {
      return handleListTasks(url, env);
    }

    // GET /agents — list agents
    if (method === 'GET' && url.pathname === '/agents') {
      return handleListAgents(url, env);
    }

    // GET /status or GET /
    if (method === 'GET' && (url.pathname === '/status' || url.pathname === '/')) {
      return handleStatus(env);
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
