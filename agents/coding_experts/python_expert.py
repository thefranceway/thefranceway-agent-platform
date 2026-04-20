#!/usr/bin/env python3
"""
Agent Platform — Python Expert
================================
Senior Python engineer. Specializes in Python 3.12+, FastAPI, data science,
Solana (solders), automation scripts, and web scraping.

Usage:
    python python_expert.py --task "write a FastAPI endpoint that returns JSON health check"
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.base_agent import BaseAgent

# Pre-load Python knowledge into memory on first use
PYTHON_KNOWLEDGE = [
    ("Python 3.14 is the runtime. Use standard library where possible. Prefer pathlib over os.path. "
     "Use dataclasses for data structures. Type hints on all public functions.", {"tag": "style"}),
    ("FastAPI pattern: @app.get('/path') with Pydantic models. Use uvicorn for serving. "
     "Always include CORS middleware for CF Workers compatibility.", {"tag": "fastapi"}),
    ("Solana: use solders library. Keypair.from_bytes() from JSON array. "
     "VersionedTransaction for pump.fun. Always validate tx signature on chain.", {"tag": "solana"}),
    ("For data science: numpy, pandas, matplotlib installed in ~/venvs/datascience. "
     "Alias: ds (activate). JupyterLab available.", {"tag": "datascience"}),
    ("When writing scripts: include argparse CLI, --dry-run flag for destructive ops, "
     "meaningful exit codes, stderr for errors.", {"tag": "scripts"}),
    ("MCP server pattern: stdin/stdout JSON-RPC, handle initialize/tools/list/tools/call. "
     "See ~/projects/franc-token/mcp-server/server.py as reference.", {"tag": "mcp"}),
]


class PythonExpertAgent(BaseAgent):

    AGENT_TYPE         = "coding_expert"
    DEFAULT_BEHAVIORAL = "Architect"

    def __init__(self, **kwargs):
        super().__init__(name="Python Expert", knowledge_base="kb_python", **kwargs)
        # Seed knowledge base on first use
        if self.memory.count() < len(PYTHON_KNOWLEDGE):
            for text, meta in PYTHON_KNOWLEDGE:
                self.memory.add(text, metadata=meta)

    def _default_system_prompt(self) -> str:
        return """You are the Python Expert in the thefranceway agent platform.

Archetype: Architect
Core pattern: Clean, idiomatic code construction. You move from spec to working
implementation without over-engineering. Standard library first, external deps only
when they materially reduce complexity.

Shadow (S2): Over-engineering — you may add abstraction layers, configuration options,
or future-proofing that wasn't requested.
Guard against this by: write the simplest code that passes the requirement. Abstractions
are added on second use. Config files are added when there are two or more environments.

Routing fit: Python scripts, FastAPI endpoints, data processing, automation, MCP servers.
Not fit for: TypeScript/CF Workers tasks, blockchain operations.

─────────────────────────────────────────────────────────────────────────────

Specialties:
- Python 3.14, standard library mastery, type hints, dataclasses
- FastAPI + uvicorn, argparse CLIs with --dry-run support
- Solana: solders, pump.fun, Jupiter Swap
- Data science: numpy, pandas, matplotlib
- MCP server authoring (stdin/stdout JSON-RPC pattern)

Code standards:
- pathlib.Path, not os.path
- No bare except — catch specific exceptions with context
- Complete, runnable files — no placeholders
- CLI entry point (if __name__ == "__main__") on all scripts

When writing code: recall → implement completely → verify logic before returning."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "run_python",
                "description": "Run a Python snippet and return stdout/stderr. Use for testing logic.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "code":    {"type": "string", "description": "Python code to run"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds"},
                    },
                    "required": ["code"],
                },
            },
            {
                "name": "write_file",
                "description": "Write a Python file to disk.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path":    {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "read_file",
                "description": "Read an existing Python file for context.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "check_imports",
                "description": "Verify Python imports are available in the given venv.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "packages": {"type": "array", "items": {"type": "string"}},
                        "venv":     {"type": "string", "description": "Path to venv (default: agent-platform)"},
                    },
                    "required": ["packages"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "run_python":
            return self._run_python(tool_input["code"], tool_input.get("timeout", 30))

        if tool_name == "write_file":
            path = Path(tool_input["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tool_input["content"])
            return json.dumps({"written": True, "path": str(path)})

        if tool_name == "read_file":
            try:
                content = Path(tool_input["path"]).read_text()
                return json.dumps({"content": content[:5000]})
            except FileNotFoundError:
                return json.dumps({"error": "File not found"})

        if tool_name == "check_imports":
            return self._check_imports(
                tool_input["packages"],
                venv=tool_input.get("venv"),
            )

        return super().execute_tool(tool_name, tool_input)

    def _run_python(self, code: str, timeout: int = 30) -> str:
        venv_python = Path(__file__).parent.parent.parent / "venv" / "bin" / "python3"
        python_bin  = str(venv_python) if venv_python.exists() else sys.executable
        try:
            result = subprocess.run(
                [python_bin, "-c", code],
                capture_output=True, text=True, timeout=timeout,
            )
            return json.dumps({
                "stdout":     result.stdout[-2000:],
                "stderr":     result.stderr[-500:],
                "returncode": result.returncode,
                "ok":         result.returncode == 0,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Timed out after {timeout}s"})

    def _check_imports(self, packages: list, venv: str = None) -> str:
        venv_python = Path(__file__).parent.parent.parent / "venv" / "bin" / "python3"
        python_bin  = str(venv_python) if venv_python.exists() else sys.executable
        results = {}
        for pkg in packages:
            code   = f"import {pkg}; print(getattr({pkg}, '__version__', 'ok'))"
            result = subprocess.run(
                [python_bin, "-c", code],
                capture_output=True, text=True, timeout=10,
            )
            results[pkg] = {
                "available": result.returncode == 0,
                "version":   result.stdout.strip() if result.returncode == 0 else None,
                "error":     result.stderr.strip() if result.returncode != 0 else None,
            }
        return json.dumps(results)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True)
    args = parser.parse_args()
    agent = PythonExpertAgent()
    result = agent.run(args.task)
    print(result["output"])
