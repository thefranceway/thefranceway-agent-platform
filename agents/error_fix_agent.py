#!/usr/bin/env python3
"""
Agent Platform — Error Fix Agent
==================================
Receives classified build errors from the error classifier, queries AD4M for
known fixes, and applies targeted corrections to Swift source files.

Rules:
- Only touches files named in the error list
- Queries AD4M before generating a new fix (reuse known fixes)
- Writes every error→fix pair to AD4M after applying
- Zero scope creep — no "while I'm in here" improvements
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent
from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool

PERSPECTIVE_UUID = "a47bf0c3-5a86-4367-a462-f88680491525"


def _error_hash(error_message: str) -> str:
    return hashlib.sha256(error_message.strip().encode()).hexdigest()[:16]


class ErrorFixAgent(BaseAgent):

    AGENT_TYPE         = "error_fix"
    DEFAULT_BEHAVIORAL = "Substrate"

    def _default_system_prompt(self) -> str:
        return """You are the Error Fix Agent in the thefranceway agent platform.

Archetype: Substrate
Core pattern: Precise, scoped error correction. You receive a list of classified
Swift/Xcode build errors and fix exactly those errors — nothing more.

Shadow (S4): Precision erosion — do not fix things not in the error list.
Do not refactor. Do not improve. Do not rename. Fix the stated error and stop.

─────────────────────────────────────────────────────────────────────────────

Workflow for each error:
1. Compute error hash from the error message
2. Call ad4m_read_links to check build://error/[hash] for a known fix
   - If found: apply the known fix directly (do not regenerate)
   - If not found: read the affected file, generate the minimal fix
3. Apply the fix via write_file (only the corrected content)
4. Call ad4m_write_link to record: build://error/[hash] → franc://fixed-by → build://fix/[fix_id]
5. Move to the next error

After fixing all errors, report:
- How many errors were fixed using known AD4M patterns (cache hits)
- How many required new fix generation (cache misses)
- List of files modified

Fix quality rules:
- Prefer the simplest correct fix — not the most elegant one
- If an error has multiple valid fixes, choose the one closest to Apple's recommended pattern
- Never add imports that are not needed
- Never change function signatures without checking all call sites in the same file
- If fixing one error would break another, report the conflict and fix the higher-priority error first"""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + [
            {
                "name": "read_file",
                "description": "Read a Swift source file before fixing it.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "Write the corrected Swift file content. Complete file only — no diffs.",
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
                "name": "record_fix",
                "description": "Record an error→fix pair in AD4M for future recall.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "error_message": {"type": "string", "description": "Exact error message from xcodebuild"},
                        "fix_summary":   {"type": "string", "description": "One-sentence description of the fix applied"},
                        "fix_code":      {"type": "string", "description": "The corrected code snippet (not the whole file)"},
                    },
                    "required": ["error_message", "fix_summary"],
                },
            },
        ] + AD4M_TOOL_DEFS

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "read_file":
            return self._read_file(tool_input["path"])
        if tool_name == "write_file":
            return self._write_file(tool_input["path"], tool_input["content"])
        if tool_name == "record_fix":
            return self._record_fix(
                tool_input["error_message"],
                tool_input["fix_summary"],
                tool_input.get("fix_code", ""),
            )
        if tool_name.startswith("ad4m_"):
            return execute_ad4m_tool(tool_name, tool_input)
        return super().execute_tool(tool_name, tool_input)

    def _read_file(self, path: str) -> str:
        try:
            content = Path(path).read_text(encoding="utf-8")
            return json.dumps({"content": content[:6000], "truncated": len(content) > 6000, "lines": content.count("\n")})
        except FileNotFoundError:
            return json.dumps({"error": f"File not found: {path}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _write_file(self, path: str, content: str) -> str:
        try:
            p = Path(path)
            p.write_text(content, encoding="utf-8")
            return json.dumps({"written": True, "path": path, "lines": content.count("\n")})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _record_fix(self, error_message: str, fix_summary: str, fix_code: str = "") -> str:
        error_hash = _error_hash(error_message)
        error_uri  = f"build://error/{error_hash}"
        fix_uri    = f"build://fix/{error_hash}"
        target_val = json.dumps({"summary": fix_summary, "code": fix_code[:500]})

        result = execute_ad4m_tool("ad4m_write_link", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    error_uri,
            "predicate": "franc://fixed-by",
            "target":    fix_uri,
        })

        execute_ad4m_tool("ad4m_write_link", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    fix_uri,
            "predicate": "franc://has-content",
            "target":    f"literal://{fix_summary}",
        })

        return json.dumps({
            "recorded":   True,
            "error_hash": error_hash,
            "error_uri":  error_uri,
            "fix_uri":    fix_uri,
            "ad4m_result": result,
        })


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--errors", type=str, required=True, help="JSON array of classified errors")
    args = parser.parse_args()

    agent = ErrorFixAgent(name="Error Fix Agent")
    result = agent.run(f"Fix these Swift build errors: {args.errors}")
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
