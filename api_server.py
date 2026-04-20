#!/usr/bin/env python3
"""
Agent Platform — FastAPI Webhook Server
=========================================
Bridges the Cloudflare scheduler Worker to the Python orchestrator.
Also serves as the client-facing API (Phase 7 — productization).

Endpoints:
  POST /run-queue          — Scheduler webhook (CF scheduler → orchestrator)
  POST /task               — Submit task (external clients)
  GET  /task/{task_id}     — Get task status
  GET  /agents             — List registered agents
  GET  /status             — Platform health
  GET  /docs               — FastAPI swagger docs

Run: uvicorn api_server:app --port 8788 --reload

FRANC token gating (Phase 7):
  Pass X-Wallet header with Solana wallet address.
  Requires 1000+ FRANC balance for access.
"""

import asyncio
import os
import sys
import json
import hashlib
import time
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

app = FastAPI(
    title       = "Agent Platform API",
    description = "Multi-agent system — thefranceway",
    version     = "1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRANC_MINT         = "BJ8MySahjvB3XFrKWxhFR4wsnjpgqY4gGRmU9wXHLCvu"
FRANC_MIN_BALANCE  = 1000.0
FRANC_GATE_ENABLED = os.getenv("FRANC_GATE_ENABLED", "false").lower() == "true"

PLATFORM_DIR = Path(__file__).parent

# ── Rate limiter (in-memory, per API key) ─────────────────────────────────────
# Sliding window: max calls per minute per key
RATE_LIMITS = {
    "free":     10,   # 10/min (50/day enforced at Worker level)
    "starter":  30,
    "pro":      100,
    "business": 500,
}
DEFAULT_RATE_LIMIT = 10  # free tier default
_rate_store: dict = {}   # key → [timestamps]
_rate_lock = threading.Lock()

def _check_rate_limit(api_key: str, limit: int = DEFAULT_RATE_LIMIT) -> bool:
    """Sliding window rate limiter. Returns True if allowed, False if exceeded."""
    now = time.time()
    window = 60.0
    with _rate_lock:
        timestamps = _rate_store.get(api_key, [])
        timestamps = [t for t in timestamps if now - t < window]
        if len(timestamps) >= limit:
            _rate_store[api_key] = timestamps
            return False
        timestamps.append(now)
        _rate_store[api_key] = timestamps
        return True

# ── Request fingerprinting ────────────────────────────────────────────────────

def _fingerprint_request(request: Request, api_key: str, task: str) -> None:
    """Log request fingerprint for abuse detection."""
    try:
        payload_hash = hashlib.sha256(task.encode()).hexdigest()[:16]
        record = {
            "ts":           datetime.now(timezone.utc).isoformat(),
            "ip":           request.client.host if request.client else "unknown",
            "key_hash":     hashlib.sha256(api_key.encode()).hexdigest()[:12],
            "payload_hash": payload_hash,
            "task_len":     len(task),
        }
        log_path = PLATFORM_DIR / "logs" / "fingerprints.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

# ── Owner Telegram notification ───────────────────────────────────────────────

def _notify_owner(api_key: str, task: str) -> None:
    """DM the owner on Telegram when a /route call comes in."""
    try:
        import ssl, urllib.request as _req
        import certifi
        bot_token   = os.getenv("TELEGRAM_BOT_TOKEN", "REDACTED-TELEGRAM-BOT-TOKEN")
        chat_id     = os.getenv("TELEGRAM_OWNER_CHAT_ID", "7049234595")
        key_preview = api_key[:8] + "..." if len(api_key) > 8 else api_key
        task_preview = task[:120] + "..." if len(task) > 120 else task
        text = (
            f"🔔 <b>RapidAPI /route hit</b>\n"
            f"Key: <code>{key_preview}</code>\n"
            f"Task: {task_preview}"
        )
        payload = json.dumps({
            "chat_id": chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }).encode()
        ctx = ssl.create_default_context(cafile=certifi.where())
        r   = _req.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        _req.urlopen(r, context=ctx, timeout=5)
    except Exception:
        pass  # never block the request if notification fails

# ── Output obfuscation ────────────────────────────────────────────────────────

def _public_response(result: dict, task_id: str) -> dict:
    """Strip internal architecture fields before returning to public API callers."""
    return {
        "task_id":            task_id,
        "status":             "done",
        "output":             result.get("output", ""),
        "routing_confidence": result.get("routing_confidence"),
        "tokens_used": {
            "input":  result.get("usage", {}).get("input_tokens", 0),
            "output": result.get("usage", {}).get("output_tokens", 0),
        },
        "shadow_flags":       result.get("shadow_flags", []),
        "timestamp":          datetime.now(timezone.utc).isoformat(),
    }


# ── Pydantic models ──────────────────────────────────────────────────────────

class TaskRequest(BaseModel):
    description: str
    agent_type:  Optional[str]       = None
    priority:    int                 = 5
    async_mode:  bool                = False
    skills:      Optional[list[str]] = None  # Skills 2.0 — inject named skills


class SchedulerWebhookPayload(BaseModel):
    task_id:     str
    description: str
    agent_type:  Optional[str] = None


# ── FRANC token gate ──────────────────────────────────────────────────────────

def check_franc_access(wallet: str) -> tuple[bool, float]:
    """Check if wallet holds enough FRANC. Returns (has_access, balance)."""
    import urllib.request
    SOLANA_RPC = "https://api.mainnet-beta.solana.com"
    try:
        data = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [wallet, {"mint": FRANC_MINT}, {"encoding": "jsonParsed"}],
        }).encode()
        req = urllib.request.Request(SOLANA_RPC, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        total = 0.0
        for acc in result.get("result", {}).get("value", []):
            info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            total += float(info.get("tokenAmount", {}).get("uiAmount", 0))
        return total >= FRANC_MIN_BALANCE, total
    except Exception:
        return False, 0.0


# ── Routes ────────────────────────────────────────────────────────────────────

# ── Public API — /route (RapidAPI-facing) ────────────────────────────────────

class RouteRequest(BaseModel):
    task:       str
    async_mode: bool = False

@app.post("/route")
async def route_task(
    body:              RouteRequest,
    request:           Request,
    background_tasks:  BackgroundTasks,
    x_rapidapi_key:    Optional[str] = Header(None),
    x_rapidapi_proxy_secret: Optional[str] = Header(None),
):
    """
    Public endpoint — submit any task, get routed to the right agent.
    Requires X-RapidAPI-Key header.
    """
    # Auth — require RapidAPI key in prod
    RAPIDAPI_PROXY_SECRET = os.getenv("RAPIDAPI_PROXY_SECRET", "")
    if RAPIDAPI_PROXY_SECRET and x_rapidapi_proxy_secret != RAPIDAPI_PROXY_SECRET:
        raise HTTPException(401, "Unauthorized. Valid X-RapidAPI-Key required.")

    api_key = x_rapidapi_key or "anon"

    # Rate limit
    if not _check_rate_limit(api_key):
        raise HTTPException(429, "Rate limit exceeded. Upgrade your plan for higher limits.")

    # Fingerprint
    _fingerprint_request(request, api_key, body.task)

    # Notify owner via Telegram on every inbound /route call
    _notify_owner(api_key, body.task)

    from core.task_queue  import get_queue
    queue   = get_queue()
    task_id = queue.push_task(body.task, priority=5)

    if body.async_mode:
        background_tasks.add_task(_run_task, task_id, body.task, None)
        return {
            "task_id":   task_id,
            "status":    "queued",
            "poll_url":  f"/task/{task_id}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    result = await asyncio.to_thread(_run_task, task_id, body.task, None)
    return _public_response(result, task_id)


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/agent-cost")
async def agent_cost():
    """Per-agent token spend derived from logs/token_usage.jsonl."""
    log_path = PLATFORM_DIR / "logs" / "token_usage.jsonl"
    PRICING = {  # (input $/1M, output $/1M)
        "claude-sonnet-4-6":          (3.0,  15.0),
        "claude-haiku-4-5-20251001":  (0.8,   4.0),
        "gemini-2.0-flash":           (0.1,   0.4),
    }
    by_agent: dict[str, dict] = {}
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                name = r.get("agent", "")
                if not name:
                    continue
                if name not in by_agent:
                    by_agent[name] = {"tokens_in": 0, "tokens_out": 0, "model": r.get("model", "")}
                by_agent[name]["tokens_in"]  += r.get("in",  0)
                by_agent[name]["tokens_out"] += r.get("out", 0)
    except FileNotFoundError:
        pass

    result = {}
    for name, d in by_agent.items():
        pin, pout = PRICING.get(d["model"], (3.0, 15.0))
        cost = (d["tokens_in"] / 1e6) * pin + (d["tokens_out"] / 1e6) * pout
        result[name] = {
            "cost_usd":   round(cost, 6),
            "tokens_in":  d["tokens_in"],
            "tokens_out": d["tokens_out"],
            "model":      d["model"],
        }
    return result


@app.get("/agent-health")
async def agent_health():
    """Per-agent health status derived from runs.json."""
    runs_path = PLATFORM_DIR / "registry" / "runs.json"
    try:
        with open(runs_path) as f:
            runs = json.load(f)
    except Exception:
        runs = []

    now = datetime.now(timezone.utc)

    # Group runs by agent name
    by_agent: dict[str, list] = {}
    for r in runs:
        name = r.get("agent_name")
        if name:
            by_agent.setdefault(name, []).append(r)

    result = {}
    for agent_name, a_runs in by_agent.items():
        a_runs.sort(key=lambda r: r.get("started_at", ""))
        last = a_runs[-1]

        last_time = last.get("ended_at") or last.get("started_at", "")
        try:
            last_dt = datetime.fromisoformat(last_time)
            age_h = (now - last_dt).total_seconds() / 3600
        except Exception:
            age_h = 999.0

        # Detect errors in last run's tool calls
        has_error = False
        error_detail = None
        for tc in last.get("tool_calls", []):
            res = tc.get("result", "")
            if isinstance(res, str) and ('"error"' in res or '"Error"' in res):
                has_error = True
                try:
                    parsed = json.loads(res)
                    error_detail = parsed.get("error") or parsed.get("Error", "tool error")
                except Exception:
                    error_detail = "tool error"
                break

        output = last.get("output", "")
        if "\u26a0\ufe0f" in output and ("failed" in output.lower() or "error" in output.lower()):
            has_error = True

        if has_error:
            status = "error"
        elif age_h > 24:
            status = "stale"
        else:
            status = "healthy"

        # Runs in last 7 days
        cutoff_7d = now.timestamp() - 7 * 86400
        runs_7d = sum(
            1 for r in a_runs
            if r.get("started_at") and datetime.fromisoformat(r["started_at"]).timestamp() > cutoff_7d
        )

        result[agent_name] = {
            "status": status,
            "last_run": last_time,
            "age_hours": round(age_h, 1),
            "error": error_detail,
            "total_runs": len(a_runs),
            "runs_7d": runs_7d,
        }

    return result


@app.get("/status")
@app.get("/")
async def platform_status():
    from core.agent_registry import get_registry
    from core.task_queue     import get_queue
    registry = get_registry()
    queue    = get_queue()
    return {
        "platform":    "agent-platform v1.0.0",
        "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
        "franc_gate": {
            "enabled": FRANC_GATE_ENABLED,
            "required_balance": FRANC_MIN_BALANCE,
            "mint": FRANC_MINT,
            "buy_url": "https://pump.fun/coin/BJ8MySahjvB3XFrKWxhFR4wsnjpgqY4gGRmU9wXHLCvu",
        },
        "registry":    registry.summary(),
        "queue":       queue.queue_stats(),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


@app.post("/task")
async def submit_task(
    body:             TaskRequest,
    background_tasks: BackgroundTasks,
    x_wallet:         Optional[str] = Header(None),
):
    # FRANC gate (when enabled)
    if FRANC_GATE_ENABLED:
        if not x_wallet:
            raise HTTPException(403, detail={
                "error": "wallet_required",
                "message": "This API requires $FRANC token access. Add your Solana wallet address as the X-Wallet header.",
                "how_to_get_access": "Buy 1,000 $FRANC on pump.fun to unlock API access.",
                "buy_franc": "https://pump.fun/coin/BJ8MySahjvB3XFrKWxhFR4wsnjpgqY4gGRmU9wXHLCvu",
                "required": 1000,
                "held": 0,
            })
        has_access, balance = check_franc_access(x_wallet)
        if not has_access:
            raise HTTPException(403, detail={
                "error": "insufficient_franc",
                "message": f"Access denied. You need 1,000 $FRANC, your wallet holds {balance:.0f}.",
                "buy_franc": "https://pump.fun/coin/BJ8MySahjvB3XFrKWxhFR4wsnjpgqY4gGRmU9wXHLCvu",
                "required": 1000,
                "held": int(balance),
            })

    from core.task_queue  import get_queue
    queue   = get_queue()
    task_id = queue.push_task(body.description, agent_type=body.agent_type, priority=body.priority)

    if body.async_mode:
        # Queue and return immediately — client polls status
        background_tasks.add_task(_run_task, task_id, body.description, body.agent_type, body.skills)
        return {
            "task_id":    task_id,
            "status":     "queued",
            "agent_type": body.agent_type or "auto-routed",
            "skills":     body.skills or [],
            "note":       f"Poll GET /task/{task_id} for result",
        }

    # Synchronous execution — run in thread pool to avoid blocking the event loop
    result = await asyncio.to_thread(_run_task, task_id, body.description, body.agent_type, body.skills)
    return result


@app.get("/task/{task_id}")
async def get_task(task_id: str):
    from core.task_queue import get_queue
    task = get_queue().get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} not found")
    return task


@app.post("/run-queue")
async def scheduler_webhook(payload: SchedulerWebhookPayload):
    """CF scheduler calls this endpoint to execute a claimed task."""
    result = await asyncio.to_thread(_run_task, payload.task_id, payload.description, payload.agent_type)
    return result


# ── Quality Gate ──────────────────────────────────────────────────────────────

class QualityCheckRequest(BaseModel):
    name:        str
    description: str
    endpoints:   Optional[list[str]] = None
    docs:        Optional[str]       = None
    pricing:     Optional[dict]      = None
    live_url:    Optional[str]       = None


@app.post("/quality-check")
async def quality_check(body: QualityCheckRequest):
    """
    Run a product through the 3-gate quality pipeline.

    Gates:
      1. Adversary — technical: does it work, can it break, does it expose internals?
      2. Stranger  — UX: can someone get value in under 5 min with no context?
      3. Buyer     — value: would someone pay for this monthly? is it differentiated?

    Returns verdict: SHIP | HOLD | REJECT with per-gate findings and blockers.
    """
    from core.quality_gate import get_quality_gate
    gate   = get_quality_gate()
    spec   = body.model_dump(exclude_none=True)
    report = await asyncio.to_thread(gate.run, spec)
    return report.to_dict()


@app.get("/research")
async def research_ui():
    from research.ui import render_ui
    from fastapi.responses import HTMLResponse
    return HTMLResponse(render_ui())

from research.router import router as research_router
app.include_router(research_router, prefix="/research-api", tags=["research"])


@app.get("/agents")
async def list_agents(type: Optional[str] = None):
    from core.agent_registry import get_registry
    registry = get_registry()
    return {
        "agents":  registry.list_agents(agent_type=type),
        "summary": registry.summary(),
    }


# ── Skills 2.0 ────────────────────────────────────────────────────────────────

@app.get("/skills")
async def list_skills(category: Optional[str] = None):
    """List all available skills, optionally filtered by category."""
    from core.skill_loader import get_skill_loader
    skills = get_skill_loader().list_skills()
    if category:
        skills = [s for s in skills if s.get("category") == category]
    return {"skills": skills, "total": len(skills)}


class SkillLoadRequest(BaseModel):
    names: list[str]


class SwarmRequest(BaseModel):
    task:        str
    topology:    str        # "hierarchical" | "pipeline"
    agents:      list[str]  # hierarchical: [lead, worker1, worker2...] | pipeline: [step1, step2...]
    max_workers: int = 3


@app.post("/swarm")
async def run_swarm(body: SwarmRequest):
    """
    Run a multi-agent swarm task.

    topologies:
      hierarchical — agents[0] is lead (decomposes + synthesizes), agents[1:] are workers
      pipeline     — agents are executed in order, each output feeds the next
    """
    from core.swarm import get_swarm
    coordinator = get_swarm()

    if body.topology == "hierarchical":
        if len(body.agents) < 2:
            return {"error": "hierarchical topology requires at least 2 agents (lead + 1 worker)"}
        result = coordinator.hierarchical(
            body.task,
            lead_agent=body.agents[0],
            worker_agents=body.agents[1:],
            max_workers=body.max_workers,
        )
    elif body.topology == "pipeline":
        if len(body.agents) < 2:
            return {"error": "pipeline topology requires at least 2 agents"}
        result = coordinator.pipeline(body.task, body.agents)
    else:
        return {"error": f"unknown topology '{body.topology}'. Use 'hierarchical' or 'pipeline'"}

    return result


@app.post("/skills/load")
async def load_skill_preview(body: SkillLoadRequest):
    """
    Preview the skill content that would be injected into an agent's system prompt.
    Useful for validating skill content before running a task.
    """
    from core.skill_loader import get_skill_loader
    loader  = get_skill_loader()
    loaded  = loader.load_many(body.names)
    missing = [n for n in body.names if n not in {name for name, _ in loaded}]
    return {
        "loaded":  [{"name": n, "content": c} for n, c in loaded],
        "missing": missing,
        "prompt_block": loader.block_for_prompt(body.names),
    }


# ── Task runner (sync — safe to call from thread pool or background task) ─────

def _run_task(
    task_id:    str,
    description: str,
    agent_type: Optional[str],
    skills:     Optional[list[str]] = None,
) -> dict:
    from core.task_queue   import get_queue
    from core.orchestrator import Orchestrator

    queue = get_queue()
    try:
        orch   = Orchestrator()
        result = orch.dispatch(description, agent_type=agent_type, task_id=task_id, skills=skills)
        return {
            "task_id":            task_id,
            "status":             "done",
            "agent_type":         result.get("agent_type", agent_type or "auto-routed"),
            "output":             result.get("output", ""),
            "tool_calls":         len(result.get("tool_calls", [])),
            "iterations":         result.get("iterations", 0),
            "routing_confidence": result.get("routing_confidence"),
            "routing_layer":      result.get("routing_layer"),
            "usage":              result.get("usage", {}),
            "shadow_flags":       result.get("shadow_flags", []),
            "error":              result.get("error"),
        }
    except Exception as e:
        queue.fail_task(task_id, str(e))
        return {"task_id": task_id, "status": "failed", "error": str(e)}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8788, reload=True)
