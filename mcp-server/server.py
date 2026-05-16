#!/usr/bin/env python3
"""
Agent Platform — MCP Server
=============================
Exposes the agent platform to Claude via the MCP protocol.
Follows the franc-token/mcp-server/server.py pattern exactly.

Tools:
  dispatch_task(description, agent_type)  — queue + run a task
  list_agents()                           — show all registered agents
  create_agent(name, description, type)   — trigger meta-agent to build a new agent
  get_task_status(task_id)                — check a task's status
  get_agent_output(task_id)               — retrieve completed task result
  platform_status()                       — registry + queue overview
  create_agent_from_archetype(...)        — MABP protocol: archetype-first agent design

Registration in ~/.claude/settings.json:
  "agent-platform": {
    "command": "/Users/multiuniverse/projects/agent-platform/venv/bin/python3",
    "args": ["/Users/multiuniverse/projects/agent-platform/mcp-server/server.py"]
  }
"""

import json
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def error_response(rid, code: int, message: str):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def text_result(rid, text: str):
    return {"jsonrpc": "2.0", "id": rid, "result": {
        "content": [{"type": "text", "text": text}]
    }}


TOOLS = [
    {
        "name":        "dispatch_task",
        "description": (
            "Queue and execute a task on the agent platform. "
            "Omit agent_type to let the orchestrator auto-route (recommended — 3-layer routing: "
            "keyword → MABP behavioral profile → LLM fallback). "
            "Specify agent_type only to force a specific agent. "
            "Returns a task_id and the agent's output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {
                    "type":        "string",
                    "description": "What you want the agent to do"
                },
                "agent_type": {
                    "type":        "string",
                    "description": "Optional — omit for auto-routing. Force a specific agent: builder | ops | meta | python | typescript | solana",
                    "enum":        ["builder", "ops", "meta", "python", "typescript", "solana"]
                },
                "async_mode": {
                    "type":        "boolean",
                    "description": "If true, queue the task and return task_id immediately (don't wait for result)"
                },
            },
            "required": ["description"],
        },
    },
    {
        "name":        "list_agents",
        "description": "List all registered agents in the platform with their type, status, and behavioral profile.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type":        "string",
                    "description": "Optional: filter by agent type"
                },
            },
        },
    },
    {
        "name":        "create_agent",
        "description": (
            "Trigger the Meta Agent to design and register a brand new agent. "
            "Provide a natural language description of what the agent should do. "
            "The Meta Agent writes the system prompt, selects tools, assigns a behavioral profile, "
            "generates a Python class, and registers it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {
                    "type":        "string",
                    "description": "Natural language description of the new agent's purpose and capabilities"
                },
            },
            "required": ["description"],
        },
    },
    {
        "name":        "get_task_status",
        "description": "Check the status of a dispatched task (pending/running/done/failed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name":        "get_agent_output",
        "description": "Retrieve the full output of a completed task, including tool call log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name":        "platform_status",
        "description": "Get an overview of the agent platform: registered agents, task queue stats, and recent runs.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name":        "clear_failed_tasks",
        "description": "Delete all failed tasks from the platform queue. Returns the count of tasks removed.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name":        "create_agent_from_archetype",
        "description": (
            "Build a new agent spec using the MABP Agent Building Protocol. "
            "Archetype-first design: answers to 4 domain questions determine the behavioral profile, "
            "shadow calibration, system prompt, and routing config automatically. "
            "The protocol ensures every agent has a declared failure mode before it is built."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type":        "string",
                    "description": "Agent name (e.g. 'Content Strategist', 'Memory Agent')",
                },
                "work_type": {
                    "type":        "string",
                    "enum":        ["research", "build", "execute", "monitor", "mission"],
                    "description": "What type of work does this agent do?",
                },
                "condition": {
                    "type":        "string",
                    "enum":        ["supervised", "autonomous", "periodic", "reactive"],
                    "description": "What operating conditions does this agent work under?",
                },
                "failure": {
                    "type":        "string",
                    "enum":        ["wrong_answer", "inaction", "scope_creep", "mission_drift"],
                    "description": "What is the costliest failure mode for this agent?",
                },
                "operator": {
                    "type":        "string",
                    "enum":        ["Sovereign", "Director", "Collaborator", "Experimenter"],
                    "description": "What is the human operator type who will use this agent?",
                },
                "specialties": {
                    "type":        "array",
                    "items":       {"type": "string"},
                    "description": "List of domain specialties (e.g. ['twitter', 'content strategy', 'threads'])",
                },
                "description": {
                    "type":        "string",
                    "description": "One-line description of what this agent does",
                },
                "register": {
                    "type":        "boolean",
                    "description": "If true, write the spec to agents.json immediately",
                    "default":     False,
                },
                "generate_file": {
                    "type":        "boolean",
                    "description": "If true, generate the Python agent class file skeleton",
                    "default":     False,
                },
            },
            "required": ["name", "work_type", "condition", "failure", "operator"],
        },
    },
    {
        "name":        "run_council",
        "description": (
            "Run the LLM Council — 6 advisors (Contrarian, First Principles, Expansionist, "
            "Outsider, Executor, The Accountant) argue your question in parallel, blind-review "
            "each other anonymously, and a Chairman delivers a verdict with one concrete next step. "
            "Pass 'feedback' to run a decision loop iteration with experiment results."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type":        "string",
                    "description": "The decision or question to evaluate",
                },
                "context": {
                    "type":        "string",
                    "description": "Background information, constraints, or relevant details (optional)",
                },
                "feedback": {
                    "type":        "string",
                    "description": (
                        "Experiment results from the previous round — triggers a loop iteration. "
                        "Leave empty for first round."
                    ),
                },
                "pack": {
                    "type":        "string",
                    "enum":        ["default", "operator"],
                    "description": (
                        "Advisor preset. 'default' = universal thinking lenses (Contrarian, First Principles, "
                        "Expansionist, Outsider, Executor, Accountant). "
                        "'operator' = company-operation lenses (CEO, CMO, CTO, Investor, Co-founder, Coach). "
                        "Use 'operator' for questions about running the business: hiring, pricing, fundraising, GTM."
                    ),
                },
            },
            "required": ["question"],
        },
    },
    {
        "name":        "run_pipeline",
        "description": (
            "Run a sequential chain of agents where each step's output feeds the next. "
            "Example: ['research', 'content_strategist'] → research synthesizes sources, "
            "content_strategist turns the synthesis into a tweet thread. "
            "Returns final output plus per-step intermediates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type":        "string",
                    "description": "The initial task description passed to the first agent",
                },
                "steps": {
                    "type":        "array",
                    "items":       {"type": "string"},
                    "description": (
                        "Ordered list of agent types. Each type is routed to the matching agent. "
                        "Supported: research, builder, ops, meta, python, typescript, content_strategist, "
                        "monitoring, longevity, coasys_watcher"
                    ),
                    "minItems": 2,
                },
                "context": {
                    "type":        "string",
                    "description": "Optional shared context prepended to the initial task",
                },
            },
            "required": ["task", "steps"],
        },
    },
]


def handle(req: dict) -> dict:
    method = req.get("method")
    rid    = req.get("id")
    params = req.get("params", {})

    # ── Protocol handshake ────────────────────────────────────────────────

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": "agent-platform", "version": "1.0.0"},
        }}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "notifications/initialized":
        return None  # No response needed

    # ── Tool calls ────────────────────────────────────────────────────────

    if method == "tools/call":
        tool = params.get("name")
        args = params.get("arguments", {})

        # ── dispatch_task ─────────────────────────────────────────────────

        if tool == "dispatch_task":
            description = args.get("description", "")
            agent_type  = args.get("agent_type")
            async_mode  = args.get("async_mode", False)

            if not description:
                return error_response(rid, -32602, "description is required")

            try:
                from core.task_queue import get_queue
                queue   = get_queue()
                task_id = queue.push_task(description, agent_type=agent_type)

                if async_mode:
                    return text_result(rid, json.dumps({
                        "task_id":    task_id,
                        "status":     "queued",
                        "agent_type": agent_type or "auto-routed",
                        "note":       "Use get_task_status to check progress",
                    }))

                # Synchronous: run now
                from core.orchestrator import Orchestrator
                orch   = Orchestrator()
                claimed = queue.claim_task()

                if claimed and claimed["id"] == task_id:
                    result = orch.dispatch(description, agent_type=agent_type, task_id=task_id)
                else:
                    # Queue race (shouldn't happen in single-user mode)
                    result = orch.dispatch(description, agent_type=agent_type)

                output = {
                    "task_id":    task_id,
                    "agent_type": result.get("agent_type", agent_type or "auto-routed"),
                    "output":     result.get("output", ""),
                    "tool_calls": len(result.get("tool_calls", [])),
                    "iterations": result.get("iterations", 0),
                    "error":      result.get("error"),
                }
                return text_result(rid, json.dumps(output, indent=2))

            except RuntimeError as e:
                # ANTHROPIC_API_KEY not set etc.
                return text_result(rid, json.dumps({"error": str(e)}))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── list_agents ───────────────────────────────────────────────────

        if tool == "list_agents":
            try:
                from core.agent_registry import get_registry
                registry = get_registry()
                agents   = registry.list_agents(agent_type=args.get("type"))
                summary  = registry.summary()
                return text_result(rid, json.dumps({
                    "total":   summary["total"],
                    "by_type": summary["by_type"],
                    "agents":  [
                        {
                            "id":      a["id"][:8] + "...",
                            "name":    a["name"],
                            "type":    a["type"],
                            "model":   a["model"],
                            "profile": a["behavioral_profile"],
                            "tools":   a.get("tools", []),
                            "kb":      a.get("knowledge_base"),
                        }
                        for a in agents
                    ],
                }, indent=2))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── create_agent ──────────────────────────────────────────────────

        if tool == "create_agent":
            description = args.get("description", "")
            if not description:
                return error_response(rid, -32602, "description is required")
            try:
                from core.orchestrator import Orchestrator
                orch   = Orchestrator()
                task   = f"Create a new agent from this specification: {description}"
                result = orch.dispatch(task, agent_type="meta")
                return text_result(rid, json.dumps({
                    "output":     result.get("output", ""),
                    "tool_calls": result.get("tool_calls", []),
                    "error":      result.get("error"),
                }, indent=2))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── get_task_status ───────────────────────────────────────────────

        if tool == "get_task_status":
            task_id = args.get("task_id", "")
            if not task_id:
                return error_response(rid, -32602, "task_id is required")
            try:
                from core.task_queue import get_queue
                task = get_queue().get_task(task_id)
                if not task:
                    return text_result(rid, json.dumps({"error": "Task not found", "task_id": task_id}))
                return text_result(rid, json.dumps({
                    "task_id":    task["id"],
                    "status":     task["status"],
                    "agent_type": task.get("agent_type"),
                    "description": task["description"][:100],
                    "created_at": task.get("created_at"),
                    "started_at": task.get("started_at"),
                    "ended_at":   task.get("ended_at"),
                    "error":      task.get("error"),
                }, indent=2))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── get_agent_output ──────────────────────────────────────────────

        if tool == "get_agent_output":
            task_id = args.get("task_id", "")
            if not task_id:
                return error_response(rid, -32602, "task_id is required")
            try:
                from core.task_queue import get_queue
                task = get_queue().get_task(task_id)
                if not task:
                    return text_result(rid, json.dumps({"error": "Task not found"}))
                output = task.get("output") or {}
                return text_result(rid, json.dumps({
                    "task_id":    task["id"],
                    "status":     task["status"],
                    "output":     output.get("output", "") if isinstance(output, dict) else str(output),
                    "tool_calls": output.get("tool_calls") if isinstance(output, dict) else None,
                    "error":      task.get("error"),
                    "ended_at":   task.get("ended_at"),
                }, indent=2))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── platform_status ───────────────────────────────────────────────

        if tool == "platform_status":
            try:
                from core.agent_registry import get_registry
                from core.task_queue     import get_queue
                registry = get_registry()
                queue    = get_queue()
                recent   = queue.list_tasks(limit=5)
                return text_result(rid, json.dumps({
                    "platform":    "agent-platform v1.0.0",
                    "registry":    registry.summary(),
                    "queue":       queue.queue_stats(),
                    "recent_tasks": [
                        {
                            "id":     t["id"][:8],
                            "desc":   t["description"][:60],
                            "status": t["status"],
                            "agent":  t.get("agent_type"),
                        }
                        for t in recent
                    ],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
                }, indent=2))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── clear_failed_tasks ────────────────────────────────────────────

        if tool == "clear_failed_tasks":
            try:
                from core.task_queue import get_queue
                count = get_queue().clear_failed_tasks()
                return text_result(rid, json.dumps({"cleared": count, "status": "ok"}, indent=2))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── create_agent_from_archetype ───────────────────────────────────

        if tool == "create_agent_from_archetype":
            required = ["name", "work_type", "condition", "failure", "operator"]
            missing  = [f for f in required if not args.get(f)]
            if missing:
                return error_response(rid, -32602, f"Missing required fields: {missing}")
            try:
                from create_agent import create_agent_from_archetype, BEHAVIORAL_PROFILES
                result = create_agent_from_archetype(
                    name        = args["name"],
                    work_type   = args["work_type"],
                    condition   = args["condition"],
                    failure     = args["failure"],
                    operator    = args["operator"],
                    specialties = args.get("specialties", []),
                    description = args.get("description", ""),
                    register    = args.get("register", False),
                    gen_file    = args.get("generate_file", False),
                )
                spec     = result["spec"]
                archetype = result["archetype"]
                profile  = BEHAVIORAL_PROFILES.get(archetype, {})

                summary = {
                    "name":               spec["name"],
                    "archetype":          archetype,
                    "shadow_code":        profile.get("shadow_code"),
                    "shadow":             profile.get("shadow", "")[:120],
                    "type":               spec["type"],
                    "tools":              spec["tools"],
                    "routing_fit":        spec["routing_fit"][:6],
                    "system_prompt":      spec["system_prompt"],
                    "registered":         result.get("registered", False),
                    "class_file":         result.get("class_file"),
                    "archetype_scores":   spec["metadata"].get("archetype_scores"),
                }
                return text_result(rid, json.dumps(summary, indent=2))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── run_council ───────────────────────────────────────────────────

        if tool == "run_council":
            question = args.get("question", "").strip()
            if not question:
                return error_response(rid, -32602, "question is required")
            try:
                from agents.council_agent import run_council, format_council_output
                result = run_council(
                    question = question,
                    context  = args.get("context", ""),
                    feedback = args.get("feedback", ""),
                    pack     = args.get("pack", "default"),
                )
                output = {
                    "verdict":    result["verdict"],
                    "next_step":  result["next_step"],
                    "condition":  result["condition"],
                    "moderation": result["moderation"],
                    "round":      result["round"],
                    "full_report": format_council_output(result),
                }
                return text_result(rid, json.dumps(output, indent=2))
            except Exception as e:
                return error_response(rid, -32603, str(e))

        # ── run_pipeline ──────────────────────────────────────────────────

        if tool == "run_pipeline":
            task  = args.get("task", "").strip()
            steps = args.get("steps", [])
            ctx   = args.get("context", "")

            if not task:
                return error_response(rid, -32602, "task is required")
            if len(steps) < 2:
                return error_response(rid, -32602, "steps must have at least 2 agent types")

            if ctx:
                task = f"{ctx}\n\n{task}"

            try:
                from core.orchestrator import Orchestrator
                from core.swarm        import SwarmCoordinator

                orch   = Orchestrator()
                swarm  = SwarmCoordinator(orch)
                result = swarm.pipeline(task=task, steps=steps)

                output = {
                    "swarm_id":    result["swarm_id"],
                    "topology":    "pipeline",
                    "steps_run":   len(result["step_results"]),
                    "final_output": result["output"],
                    "step_results": [
                        {
                            "step":       s["step"],
                            "agent_type": s["agent_type"],
                            "output":     s["output"][:500] + ("..." if len(s["output"]) > 500 else ""),
                        }
                        for s in result["step_results"]
                    ],
                }
                return text_result(rid, json.dumps(output, indent=2))

            except Exception as e:
                return error_response(rid, -32603, str(e))

        return error_response(rid, -32601, f"Unknown tool: {tool}")

    return error_response(rid, -32601, f"Unknown method: {method}")


# ── Main loop ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
            if resp is not None:
                send(resp)
        except json.JSONDecodeError as e:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": f"Parse error: {e}"}})
        except Exception as e:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32603, "message": str(e)}})
