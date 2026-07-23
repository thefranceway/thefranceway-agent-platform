/**
 * Agent Platform — Scheduler Worker
 * ====================================
 * Cron Trigger: runs every 5 minutes, polls D1 task queue,
 * triggers the Python orchestrator webhook for pending tasks.
 *
 * Also handles manual trigger via POST /run-queue
 *
 * Deploy: wrangler deploy (from workers/scheduler/)
 * Test:   wrangler dev --test-scheduled
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

type Env = {
  DB: D1Database;
  WEBHOOK_URL: string;
  PLATFORM_VERSION: string;
  PLATFORM_API_KEY: string;
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
}

// ── Core scheduler logic ──────────────────────────────────────────────────────

async function processPendingTasks(env: Env): Promise<{ processed: number; errors: number }> {
  let processed = 0;
  let errors    = 0;

  // Claim up to 5 pending tasks per cron tick
  for (let i = 0; i < 5; i++) {
    const task = await env.DB.prepare(`
      SELECT * FROM tasks
      WHERE status = 'pending'
      ORDER BY priority ASC, created_at ASC
      LIMIT 1
    `).first<{
      id: string;
      description: string;
      agent_type: string | null;
    }>();

    if (!task) break;

    // Mark as running
    await env.DB.prepare(`
      UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?
    `).bind(new Date().toISOString(), task.id).run();

    // Notify orchestrator webhook (Python server)
    try {
      const webhookUrl = env.WEBHOOK_URL || 'http://localhost:8788/run-queue';
      const resp = await fetch(webhookUrl, {
        method:  'POST',
        headers: {
          'Content-Type':  'application/json',
          // Required whenever the Python server has PLATFORM_API_KEY set —
          // without this, /run-queue 401s on every call and the task below
          // gets reset to pending forever (see the 401 branch below).
          'Authorization': `Bearer ${env.PLATFORM_API_KEY ?? ''}`,
        },
        body:    JSON.stringify({
          task_id:     task.id,
          description: task.description,
          agent_type:  task.agent_type,
        }),
        signal: AbortSignal.timeout(30_000),
      });

      if (resp.ok) {
        const result = await resp.json() as { output?: string; error?: string };
        await env.DB.prepare(`
          UPDATE tasks
          SET status = ?, output = ?, ended_at = ?
          WHERE id = ?
        `).bind(
          result.error ? 'failed' : 'done',
          JSON.stringify(result),
          new Date().toISOString(),
          task.id,
        ).run();
        processed++;
      } else if (resp.status === 401) {
        // Auth failure will never succeed on retry without a config fix —
        // resetting to pending and continuing the loop just retries the
        // same 401 every 5 minutes forever. Fail this tick loudly instead.
        await env.DB.prepare(`
          UPDATE tasks SET status = 'pending', started_at = NULL WHERE id = ?
        `).bind(task.id).run();
        console.error(`[scheduler] 401 from ${webhookUrl} — check PLATFORM_API_KEY matches the Python server's env var`);
        errors++;
        break;
      } else {
        // Webhook call failed — reset to pending for retry
        await env.DB.prepare(`
          UPDATE tasks SET status = 'pending', started_at = NULL WHERE id = ?
        `).bind(task.id).run();
        errors++;
      }
    } catch (e) {
      // Network error — reset to pending
      await env.DB.prepare(`
        UPDATE tasks SET status = 'pending', started_at = NULL WHERE id = ?
      `).bind(task.id).run();
      errors++;
      break; // Stop trying if webhook is unreachable
    }
  }

  return { processed, errors };
}

// ── Main handler ──────────────────────────────────────────────────────────────

export default {
  // HTTP handler — manual trigger
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    const url = new URL(request.url);

    if (request.method === 'POST' && url.pathname === '/run-queue') {
      const result = await processPendingTasks(env);
      return json({
        triggered:  'manual',
        ...result,
        timestamp: new Date().toISOString(),
      });
    }

    if (request.method === 'GET' && url.pathname === '/status') {
      const pending = await env.DB.prepare(
        "SELECT COUNT(*) as n FROM tasks WHERE status = 'pending'"
      ).first<{ n: number }>();

      return json({
        scheduler:  'agent-scheduler',
        version:    env.PLATFORM_VERSION ?? '1.0.0',
        pending:    pending?.n ?? 0,
        cron:       '*/5 * * * *',
        webhook:    env.WEBHOOK_URL,
        timestamp:  new Date().toISOString(),
      });
    }

    return json({
      error:     'Not found',
      endpoints: {
        'POST /run-queue': 'Manually trigger queue processing',
        'GET /status':     'Scheduler status',
      },
    }, 404);
  },

  // Scheduled handler — cron trigger
  async scheduled(
    _controller: ScheduledController,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    ctx.waitUntil(processPendingTasks(env));
  },
};
