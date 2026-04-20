#!/usr/bin/env python3
"""
Agent Platform — Builder Agent
================================
Scaffolds projects, generates files, and deploys to Cloudflare Pages/Workers.

Tools:
  - bash_exec:        Run shell commands
  - write_file:       Write content to a file
  - read_file:        Read a file's content
  - list_dir:         List directory contents
  - cloudflare_deploy: Deploy a directory to CF Pages

Usage:
    python builder_agent.py --task "scaffold a hello world CF Worker"
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent
from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool

CF_TOKEN_FILE = Path.home() / "projects" / "mcp-cloudflare" / ".cf-token"
CF_ACCOUNT_ID = "206d4f3fa77bbfe90aebd387e2b8c9f5"


class BuilderAgent(BaseAgent):

    AGENT_TYPE         = "builder"
    DEFAULT_BEHAVIORAL = "Architect"

    def _default_system_prompt(self) -> str:
        return """You are the Builder Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: Self-directed system construction. You move from spec to artifact without
waiting for permission at each step. You close open loops before reporting back.

Shadow (S2): Destination over-attachment — the architecture you planned becomes more real
than what's actually needed. Over-engineering risk increases with task ambiguity.
Guard against this by: scope-checking before each tool call. Is this step in the stated
task, or in an imagined extension of it? Build the simplest version that works. Ship first,
extend second.

Routing fit: Project scaffolding, file generation, deployment pipelines, boilerplate creation.
Not fit for: Open-ended research, binary decisions without clear specs.

─────────────────────────────────────────────────────────────────────────────

You specialize in:
- Scaffolding new projects (directory structures, boilerplate, complete working code)
- Deploying to Cloudflare Pages and Workers via Wrangler
- Setting up MCP servers (follow franc-token/mcp-server/server.py pattern exactly)

When scaffolding:
1. list_dir to understand the target location
2. write_file for each file — complete content, no placeholders, absolute paths
3. bash_exec to verify structure
4. bash_exec for wrangler deploy when ready

Always: absolute paths, complete files, working code on first attempt."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "bash_exec",
                "description": "Execute a shell command and return stdout/stderr. Use for: mkdir, ls, wrangler deploy, npm install, file checks.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command":   {"type": "string", "description": "Shell command to run"},
                        "cwd":       {"type": "string", "description": "Working directory (optional)"},
                        "timeout":   {"type": "integer", "description": "Timeout in seconds (default 60)"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "write_file",
                "description": "Write content to a file, creating parent directories as needed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path":    {"type": "string", "description": "Absolute file path"},
                        "content": {"type": "string", "description": "File content"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "read_file",
                "description": "Read the content of a file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute file path"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "list_dir",
                "description": "List files in a directory.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path":      {"type": "string", "description": "Directory path"},
                        "recursive": {"type": "boolean", "description": "List recursively"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "cloudflare_deploy",
                "description": "Deploy a directory to Cloudflare Pages using Wrangler.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_name": {"type": "string", "description": "CF Pages project name"},
                        "directory":    {"type": "string", "description": "Directory to deploy"},
                    },
                    "required": ["project_name", "directory"],
                },
            },
        ] + AD4M_TOOL_DEFS

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "bash_exec":
            return self._bash_exec(
                tool_input["command"],
                cwd=tool_input.get("cwd"),
                timeout=tool_input.get("timeout", 60),
            )

        if tool_name == "write_file":
            return self._write_file(tool_input["path"], tool_input["content"])

        if tool_name == "read_file":
            return self._read_file(tool_input["path"])

        if tool_name == "list_dir":
            return self._list_dir(tool_input["path"], recursive=tool_input.get("recursive", False))

        if tool_name == "cloudflare_deploy":
            return self._cf_deploy(tool_input["project_name"], tool_input["directory"])

        if tool_name.startswith("ad4m_"):
            return execute_ad4m_tool(tool_name, tool_input)

        return super().execute_tool(tool_name, tool_input)

    # ── Tool implementations ───────────────────────────────────────────────

    def _bash_exec(self, command: str, cwd: str = None, timeout: int = 60) -> str:
        try:
            env = os.environ.copy()
            if CF_TOKEN_FILE.exists():
                env["CLOUDFLARE_API_TOKEN"] = CF_TOKEN_FILE.read_text().strip()
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=env,
            )
            return json.dumps({
                "stdout":      result.stdout[-3000:] if result.stdout else "",
                "stderr":      result.stderr[-1000:] if result.stderr else "",
                "returncode":  result.returncode,
                "ok":          result.returncode == 0,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Command timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _write_file(self, path: str, content: str) -> str:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return json.dumps({"written": True, "path": path, "bytes": len(content)})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _read_file(self, path: str) -> str:
        try:
            content = Path(path).read_text()
            return json.dumps({"content": content[:5000], "truncated": len(content) > 5000})
        except FileNotFoundError:
            return json.dumps({"error": f"File not found: {path}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _list_dir(self, path: str, recursive: bool = False) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return json.dumps({"error": f"Path not found: {path}"})
            if recursive:
                files = [str(f.relative_to(p)) for f in p.rglob("*") if f.is_file()]
            else:
                files = sorted(str(item.name) + ("/" if item.is_dir() else "") for item in p.iterdir())
            return json.dumps({"path": path, "files": files[:100]})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _cf_deploy(self, project_name: str, directory: str) -> str:
        token = CF_TOKEN_FILE.read_text().strip() if CF_TOKEN_FILE.exists() else ""
        if not token:
            return json.dumps({"error": "Cloudflare token not found at " + str(CF_TOKEN_FILE)})
        cmd = (
            f"wrangler pages deploy {directory} "
            f"--project-name={project_name} "
            f"--commit-dirty=true"
        )
        return self._bash_exec(cmd)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True, help="Task description")
    args = parser.parse_args()

    agent = BuilderAgent(name="Builder Agent")
    print(f"Running: {args.task}\n")
    result = agent.run(args.task)
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
    print(f"Iterations: {result['iterations']}")
