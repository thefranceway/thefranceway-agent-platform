export interface Env {
  RATE_LIMIT_KV: KVNamespace;
}

interface RateLimitRecord {
  count: number;
  windowStart: number;
}

const WINDOW_SECONDS = 60;       // rolling window duration
const MAX_REQUESTS   = 30;       // max requests per window per IP

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function jsonResponse(
  body: Record<string, unknown>,
  status: number,
  extra: Record<string, string> = {}
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...CORS_HEADERS,
      ...extra,
    },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // ── CORS pre-flight ──────────────────────────────────────────────────────
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // ── Identify client IP ───────────────────────────────────────────────────
    const ip =
      request.headers.get("CF-Connecting-IP") ??
      request.headers.get("X-Forwarded-For")?.split(",")[0].trim() ??
      "unknown";

    const kvKey = `rl:${ip}`;
    const now   = Date.now();

    // ── Read existing record ─────────────────────────────────────────────────
    const raw = await env.RATE_LIMIT_KV.get(kvKey);
    let record: RateLimitRecord = raw
      ? (JSON.parse(raw) as RateLimitRecord)
      : { count: 0, windowStart: now };

    // ── Fixed-window reset ───────────────────────────────────────────────────
    const windowMs      = WINDOW_SECONDS * 1000;
    const windowExpired = now - record.windowStart >= windowMs;

    if (windowExpired) {
      record = { count: 0, windowStart: now };
    }

    // ── Increment ────────────────────────────────────────────────────────────
    record.count += 1;

    // ── Persist with TTL (2× window so KV auto-cleans stale keys) ───────────
    await env.RATE_LIMIT_KV.put(kvKey, JSON.stringify(record), {
      expirationTtl: WINDOW_SECONDS * 2,
    });

    // ── Rate-limit headers ───────────────────────────────────────────────────
    const remaining    = Math.max(0, MAX_REQUESTS - record.count);
    const resetSeconds = Math.ceil(
      (record.windowStart + windowMs - now) / 1000
    );

    const rateLimitHeaders: Record<string, string> = {
      "X-RateLimit-Limit":     String(MAX_REQUESTS),
      "X-RateLimit-Remaining": String(remaining),
      "X-RateLimit-Reset":     String(Math.floor((record.windowStart + windowMs) / 1000)),
    };

    // ── Enforce limit ────────────────────────────────────────────────────────
    if (record.count > MAX_REQUESTS) {
      return jsonResponse(
        {
          error:       "Too Many Requests",
          retryAfter:  resetSeconds,
          limit:       MAX_REQUESTS,
          windowSeconds: WINDOW_SECONDS,
        },
        429,
        {
          ...rateLimitHeaders,
          "Retry-After": String(resetSeconds),
        }
      );
    }

    // ── Pass-through: return a success response (replace with your own logic) ─
    return jsonResponse(
      {
        message:   "Request accepted",
        ip,
        count:     record.count,
        limit:     MAX_REQUESTS,
        remaining,
        resetInSeconds: resetSeconds,
      },
      200,
      rateLimitHeaders
    );
  },
} satisfies ExportedHandler<Env>;
