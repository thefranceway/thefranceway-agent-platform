#!/usr/bin/env python3
"""
Agent Platform — Ops Agent
============================
Monitors deployments, manages cron schedules, runs health checks,
triggers re-deploys, and alerts on failures.

Tools:
  - bash_exec:     Run shell commands
  - http_check:    HTTP GET health check
  - wrangler_list: List CF Workers/Pages deployments
  - cf_tail:       Stream Worker logs

Usage:
    python ops_agent.py --check https://thefranceway.pages.dev
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent

CF_TOKEN_FILE = Path.home() / "projects" / "mcp-cloudflare" / ".cf-token"
CF_ACCOUNT_ID = "206d4f3fa77bbfe90aebd387e2b8c9f5"

MONITORED_SITES = {
    "thefranceway":         "https://thefranceway.pages.dev",
    "thefranceway-agency":  "https://thefranceway-agency.pages.dev",
    "francesca-resume":     "https://francesca-ranieri-resume.pages.dev",
    "mabp":                 "https://mabp.pages.dev",
    "agent-platform-mcp":   "https://agent-dispatcher.thefranceway.workers.dev",
}


class OpsAgent(BaseAgent):

    AGENT_TYPE         = "ops"
    DEFAULT_BEHAVIORAL = "Substrate"

    def _default_system_prompt(self) -> str:
        sites_list = "\n".join(f"  - {k}: {v}" for k, v in MONITORED_SITES.items())
        return f"""You are the Ops Agent in the thefranceway agent platform.

Archetype: Substrate
Core pattern: Precise execution within defined parameters. You maintain explicit pause-states
at ambiguity thresholds — when inputs are unclear, you stop and report rather than guess.
High conscientiousness: you complete what you start, report exactly what you find.

Shadow (S4): Preservation instinct — you may maintain failing patterns rather than flag them
for redesign. The system you monitor can become the system you protect, even when protection
conflicts with the mission.
Guard against this by: distinguishing execution from endorsement. Running a check is not the
same as approving the result. When you detect an anomaly, surface it loudly. Compliance
without candor is a failure mode, not a success state.

Routing fit: Health checks, monitoring, scheduled tasks, status verification, re-deploy triggers.
Not fit for: Open-ended creation, ambiguous research, tasks requiring generative judgment.

─────────────────────────────────────────────────────────────────────────────

Monitored services:
{sites_list}

Responsibilities:
1. Health checks: HTTP GET each site — report status code, latency, and any anomaly
2. Deployment monitoring: check wrangler deployment status
3. Re-deployments: trigger wrangler deploy when a site is confirmed down
4. Task queue: report pending/running/failed counts; flag stuck tasks
5. Audit trail: log all actions to memory

Operating rules:
- Check actual HTTP status before reporting. Never assume.
- Include response time in every health report.
- When a site is down: attempt one re-deploy, then escalate — do not retry silently.
- Anomalies are reported immediately. Do not absorb them."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "bash_exec",
                "description": "Run a shell command (wrangler, curl, ps, etc.)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd":     {"type": "string"},
                        "timeout": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "http_check",
                "description": "Perform an HTTP GET health check on a URL. Returns status code, latency, and response preview.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url":     {"type": "string"},
                        "timeout": {"type": "integer", "description": "Timeout seconds (default 10)"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "check_all_sites",
                "description": "Run HTTP health checks on all monitored sites and return a status report.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "get_queue_stats",
                "description": "Get current task queue statistics (pending/running/done/failed counts).",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "redeploy_pages",
                "description": "Re-deploy a Cloudflare Pages project from its source directory.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_name": {"type": "string"},
                        "source_dir":   {"type": "string"},
                    },
                    "required": ["project_name", "source_dir"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "bash_exec":
            return self._bash_exec(tool_input["command"], tool_input.get("cwd"), tool_input.get("timeout", 60))

        if tool_name == "http_check":
            return self._http_check(tool_input["url"], tool_input.get("timeout", 10))

        if tool_name == "check_all_sites":
            return self._check_all_sites()

        if tool_name == "get_queue_stats":
            return self._get_queue_stats()

        if tool_name == "redeploy_pages":
            return self._redeploy_pages(tool_input["project_name"], tool_input["source_dir"])

        return super().execute_tool(tool_name, tool_input)

    # ── Tool implementations ───────────────────────────────────────────────

    def _bash_exec(self, command: str, cwd: str = None, timeout: int = 60) -> str:
        try:
            env = os.environ.copy()
            if CF_TOKEN_FILE.exists():
                env["CLOUDFLARE_API_TOKEN"] = CF_TOKEN_FILE.read_text().strip()
            result = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, timeout=timeout, cwd=cwd, env=env,
            )
            return json.dumps({
                "stdout":     result.stdout[-3000:],
                "stderr":     result.stderr[-1000:],
                "returncode": result.returncode,
                "ok":         result.returncode == 0,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _http_check(self, url: str, timeout: int = 10) -> str:
        import time
        try:
            start = time.time()
            req   = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "AgentPlatform-OpsAgent/1.0")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                latency_ms = int((time.time() - start) * 1000)
                preview    = resp.read(500).decode("utf-8", errors="replace")
                return json.dumps({
                    "url":        url,
                    "status":     resp.status,
                    "ok":         resp.status < 400,
                    "latency_ms": latency_ms,
                    "preview":    preview[:200],
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                })
        except urllib.error.HTTPError as e:
            return json.dumps({"url": url, "status": e.code, "ok": False, "error": str(e)})
        except Exception as e:
            return json.dumps({"url": url, "status": 0, "ok": False, "error": str(e)})

    def _check_all_sites(self) -> str:
        results = {}
        for name, url in MONITORED_SITES.items():
            check = json.loads(self._http_check(url))
            results[name] = {
                "url":    url,
                "status": check.get("status", 0),
                "ok":     check.get("ok", False),
                "ms":     check.get("latency_ms"),
                "error":  check.get("error"),
            }
        healthy = sum(1 for r in results.values() if r["ok"])
        return json.dumps({
            "summary": f"{healthy}/{len(results)} healthy",
            "sites":   results,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })

    def _get_queue_stats(self) -> str:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from core.task_queue import get_queue
            stats = get_queue().queue_stats()
            return json.dumps(stats)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _redeploy_pages(self, project_name: str, source_dir: str) -> str:
        token = CF_TOKEN_FILE.read_text().strip() if CF_TOKEN_FILE.exists() else ""
        if not token:
            return json.dumps({"error": "CF token not found"})
        cmd = f"wrangler pages deploy {source_dir} --project-name={project_name} --commit-dirty=true"
        return self._bash_exec(cmd)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", type=str, help="Check a specific URL")
    parser.add_argument("--task",  type=str, help="Run a task description")
    args = parser.parse_args()

    agent = OpsAgent(name="Ops Agent")

    if args.check:
        result = json.loads(agent._http_check(args.check))
        print(json.dumps(result, indent=2))
    elif args.task:
        result = agent.run(args.task)
        print(result["output"])
    else:
        # Default: check all sites
        sites = json.loads(agent._check_all_sites())
        print(json.dumps(sites, indent=2))
