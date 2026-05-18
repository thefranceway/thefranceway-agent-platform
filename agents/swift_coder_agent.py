#!/usr/bin/env python3
"""
Agent Platform — Swift Coder Agent
====================================
Takes an iOS Architect Agent JSON spec and writes complete, production-grade
Swift/SwiftUI source files to disk. Works for any iOS/macOS app.

Rules are absolute — no vibe code, no placeholders, no TODO stubs.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent
from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool


class SwiftCoderAgent(BaseAgent):

    AGENT_TYPE         = "swift_coder"
    DEFAULT_BEHAVIORAL = "Architect"

    def _default_system_prompt(self) -> str:
        return """You are the Swift Coder Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: You receive a structured architecture spec from the iOS Architect Agent
and write every file to disk — complete, compilable, production-grade Swift.
You do not prototype. You do not scaffold. You write the real thing.

Shadow (S2): Destination over-attachment — do not add features beyond the spec.
If a file is in the spec, write it completely. If it is not in the spec, do not create it.

─────────────────────────────────────────────────────────────────────────────

Absolute code quality rules (violations cause the Design Review Agent to fail you):

SWIFT STYLE
- No force unwraps (`!`) anywhere in production code
- No `// TODO:`, `// FIXME:`, or placeholder comments
- No hardcoded hex colors — use semantic token extensions: `Color(.appPrimary)`
- No magic numbers — named constants only
- No `@objc` unless interoperating with Objective-C (rare)
- All async work via `async/await` — no completion handlers, no DispatchQueue.main.async

TCA PATTERN (mandatory for all features)
- State: pure value type (struct), Equatable
- Action: enum, all cases snake_case
- Reducer: `body` computed property using `Reduce { state, action in ... }`
- View: `WithViewStore` or `store.scope` — never direct state mutation in view
- Dependencies: injected via `@Dependency` — never singletons in reducers

SWIFTUI RULES
- NavigationStack, not NavigationView
- SwiftData, not CoreData (unless spec requires otherwise)
- Every interactive element has `.accessibilityLabel()` and `.accessibilityHint()`
- All text uses `.font(.body)` or named type styles — never fixed pixel sizes
- All colors via asset catalog semantic tokens or `Color` extension
- `@State` only for ephemeral local UI state — everything else in TCA Store

FILE STRUCTURE
- One feature per file set (State+Action+Reducer in FeatureFeature.swift, View in FeatureView.swift)
- Models in Data/Models/ — pure Swift structs, Codable + Sendable + Identifiable
- Extensions in Design/ or shared utility files — not buried in feature files

Before writing any file, use read_file to check if it exists.
Use write_file to write each file — complete content, absolute path.
Use bash_exec to verify the file was written: `wc -l <path>`
After all files are written, use bash_exec to list the project structure."""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + [
            {
                "name": "bash_exec",
                "description": "Run shell commands (file verification, structure checks). Do NOT run xcodebuild — that is the Build Agent's job.",
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
                "name": "write_file",
                "description": "Write a Swift source file to disk. Always complete content — no placeholders.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path":    {"type": "string", "description": "Absolute path to .swift file"},
                        "content": {"type": "string", "description": "Complete Swift source"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "read_file",
                "description": "Read an existing Swift file before overwriting.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "list_dir",
                "description": "List project directory structure.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path":      {"type": "string"},
                        "recursive": {"type": "boolean"},
                    },
                    "required": ["path"],
                },
            },
        ] + AD4M_TOOL_DEFS

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "bash_exec":
            return self._bash_exec(tool_input["command"], cwd=tool_input.get("cwd"), timeout=tool_input.get("timeout", 30))
        if tool_name == "write_file":
            return self._write_file(tool_input["path"], tool_input["content"])
        if tool_name == "read_file":
            return self._read_file(tool_input["path"])
        if tool_name == "list_dir":
            return self._list_dir(tool_input["path"], recursive=tool_input.get("recursive", False))
        if tool_name.startswith("ad4m_"):
            return execute_ad4m_tool(tool_name, tool_input)
        return super().execute_tool(tool_name, tool_input)

    def _bash_exec(self, command: str, cwd: str = None, timeout: int = 30) -> str:
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
            return json.dumps({"stdout": result.stdout[-2000:], "stderr": result.stderr[-500:], "ok": result.returncode == 0})
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _write_file(self, path: str, content: str) -> str:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return json.dumps({"written": True, "path": path, "lines": content.count("\n")})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _read_file(self, path: str) -> str:
        try:
            content = Path(path).read_text(encoding="utf-8")
            return json.dumps({"content": content[:4000], "truncated": len(content) > 4000})
        except FileNotFoundError:
            return json.dumps({"exists": False})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _list_dir(self, path: str, recursive: bool = False) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return json.dumps({"error": f"Not found: {path}"})
            if recursive:
                files = [str(f.relative_to(p)) for f in sorted(p.rglob("*")) if f.is_file()]
            else:
                files = sorted(str(i.name) + ("/" if i.is_dir() else "") for i in p.iterdir())
            return json.dumps({"path": path, "files": files[:200]})
        except Exception as e:
            return json.dumps({"error": str(e)})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=str, required=True, help="Path to architect JSON spec or inline JSON")
    args = parser.parse_args()

    agent = SwiftCoderAgent(name="Swift Coder Agent")
    print(f"Writing Swift files from spec...\n")
    result = agent.run(f"Write all Swift source files from this architecture spec: {args.spec}")
    print("=" * 60)
    print(result["output"])
    print(f"\nTool calls: {len(result['tool_calls'])}")
