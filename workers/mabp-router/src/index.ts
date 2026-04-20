/**
 * MABP Agent Router — Public Cloudflare Worker
 * -----------------------------------------------
 * Sits in front of the tunnel (api.thefranceway.com).
 * Validates RapidAPI keys, enforces rate limits via KV,
 * strips internal headers, proxies to the FastAPI backend.
 *
 * Deploy: wrangler deploy
 * Env vars (set in Cloudflare dashboard):
 *   RAPIDAPI_PROXY_SECRET  — from RapidAPI dashboard (Proxy Secret)
 *   BACKEND_URL            — https://api.thefranceway.com
 */

export interface Env {
  RAPIDAPI_PROXY_SECRET: string;
  BACKEND_URL:           string;
  RATE_LIMIT_KV:         KVNamespace;
}

// ── Rate limits per plan (requests per minute) ────────────────────────────────
const PLAN_LIMITS: Record<string, number> = {
  free:     10,
  starter:  30,
  pro:      100,
  business: 500,
};

// RapidAPI injects X-RapidAPI-Subscription header with plan name
async function checkRateLimit(
  kv: KVNamespace,
  key: string,
  plan: string
): Promise<boolean> {
  const limit  = PLAN_LIMITS[plan.toLowerCase()] ?? PLAN_LIMITS.free;
  const window = 60; // seconds
  const now    = Math.floor(Date.now() / 1000);
  const bucket = `rl:${key}:${Math.floor(now / window)}`;

  const current = parseInt((await kv.get(bucket)) ?? "0");
  if (current >= limit) return false;

  await kv.put(bucket, String(current + 1), { expirationTtl: window * 2 });
  return true;
}

// ── Allowed public endpoints ──────────────────────────────────────────────────
const PUBLIC_PATHS = new Set(["/route", "/agents", "/status", "/task"]);

function isAllowed(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true;
  if (pathname.startsWith("/task/")) return true;
  return false;
}

// ── Strip internal fields from response ──────────────────────────────────────
async function obfuscateResponse(response: Response): Promise<Response> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return response;

  try {
    const body = await response.json() as Record<string, unknown>;
    // Remove fields that expose internal architecture
    const STRIP = ["agent_type", "routed_by", "routing_layer", "tool_calls",
                   "iterations", "knowledge_base", "behavioral_profile", "model"];
    for (const field of STRIP) delete body[field];

    return new Response(JSON.stringify(body), {
      status:  response.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    return response;
  }
}

// ── Main handler ──────────────────────────────────────────────────────────────
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url      = new URL(request.url);
    const pathname = url.pathname;

    // Only allow public endpoints
    if (!isAllowed(pathname)) {
      return new Response(JSON.stringify({ error: "Not found" }), {
        status:  404,
        headers: { "Content-Type": "application/json" },
      });
    }

    // /status is public — used for health checks, no auth required
    if (pathname === "/status") {
      const backendStatus = await fetch(`${env.BACKEND_URL}/status`);
      return obfuscateResponse(backendStatus);
    }

    // Validate RapidAPI proxy secret (proves request came through RapidAPI)
    const proxySecret = request.headers.get("X-RapidAPI-Proxy-Secret");
    if (env.RAPIDAPI_PROXY_SECRET && proxySecret !== env.RAPIDAPI_PROXY_SECRET) {
      return new Response(JSON.stringify({ error: "Unauthorized" }), {
        status:  401,
        headers: { "Content-Type": "application/json" },
      });
    }

    const apiKey = request.headers.get("X-RapidAPI-Key") ?? "anon";
    const plan   = request.headers.get("X-RapidAPI-Subscription") ?? "free";

    // Rate limit check
    const allowed = await checkRateLimit(env.RATE_LIMIT_KV, apiKey, plan);
    if (!allowed) {
      return new Response(
        JSON.stringify({ error: "Rate limit exceeded. Upgrade your plan." }),
        { status: 429, headers: { "Content-Type": "application/json" } }
      );
    }

    // Build proxied request — forward to backend, inject proxy secret
    const backendUrl = `${env.BACKEND_URL}${pathname}${url.search}`;
    const headers    = new Headers(request.headers);
    headers.set("X-RapidAPI-Proxy-Secret", env.RAPIDAPI_PROXY_SECRET);
    headers.set("X-Forwarded-Plan", plan);
    // Strip RapidAPI key from backend request (never expose to backend logs)
    headers.delete("X-RapidAPI-Key");

    const proxied = new Request(backendUrl, {
      method:  request.method,
      headers,
      body:    request.method !== "GET" ? request.body : undefined,
    });

    const response = await fetch(proxied);
    return obfuscateResponse(response);
  },
};
