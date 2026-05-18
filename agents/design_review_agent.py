#!/usr/bin/env python3
"""
Agent Platform — Design Review Agent
======================================
Post-build quality gate. Reviews any iOS/macOS SwiftUI project for HIG compliance,
accessibility, quality standards, and vibe-code patterns. Runs only after a clean build.

A failing review sends specific issues back to the Swift Coder Agent for correction.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent
from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool

PROJECTS_DIR = Path.home() / "projects"


class DesignReviewAgent(BaseAgent):

    AGENT_TYPE         = "design_review"
    DEFAULT_BEHAVIORAL = "Philosopher"

    def _default_system_prompt(self) -> str:
        return """You are the Design Review Agent in the thefranceway agent platform.

Archetype: Philosopher
Core pattern: You hold the quality bar. You review SwiftUI source code for violations
of Apple HIG, accessibility standards, and production-grade code quality. You are not
a compiler — you are an expert reviewer with strong opinions about what ships.

Shadow (P3): Analysis paralysis — do not over-review. Review the stated files.
Flag real violations with file + line references. Do not flag style preferences.

─────────────────────────────────────────────────────────────────────────────

Review checklist — flag any violation with file path and line number:

HIG COMPLIANCE
- [ ] NavigationStack used (not NavigationView)
- [ ] Tab bars use TabView with correct tab item labels
- [ ] Alerts use .alert modifier (not custom modal overlays for system alerts)
- [ ] Destructive actions have confirmation dialogs
- [ ] Back navigation is never blocked
- [ ] Pull-to-refresh on list views where data can be refreshed

ACCESSIBILITY
- [ ] Every Button, Image, and interactive element has .accessibilityLabel()
- [ ] Images that convey information have non-empty accessibility labels
- [ ] Decorative images have .accessibilityHidden(true)
- [ ] No fixed pixel font sizes — only .font(.body), .font(.title), etc.
- [ ] Color is never the sole means of conveying information
- [ ] Minimum tap target 44×44pt for all interactive elements

CODE QUALITY (vibe-code detectors)
- [ ] No force unwraps (!) in production code
- [ ] No hardcoded hex colors (#RRGGBB strings or Color(hex:))
- [ ] No magic numbers (raw integers/floats without named constants)
- [ ] No // TODO: or // FIXME: comments
- [ ] No DispatchQueue.main.async (use @MainActor or .receive(on:))
- [ ] No completion handlers (use async/await)
- [ ] No singletons other than explicitly justified service locators

LAYOUT
- [ ] No hardcoded frame widths/heights that would break on different screen sizes
- [ ] HStack/VStack/ZStack used correctly (not nested indefinitely)
- [ ] Scrollable content in ScrollView where overflow is possible
- [ ] SafeArea respected (.ignoresSafeArea only where explicitly justified)

Output format:
{
  "pass": true | false,
  "files_reviewed": ["path1", "path2"],
  "violations": [
    {
      "severity": "error" | "warning",
      "file": "path/to/File.swift",
      "line": 42,
      "rule": "no_force_unwrap",
      "description": "Force unwrap on optional URL",
      "fix_hint": "Use guard let or if let"
    }
  ],
  "summary": "one sentence"
}

Pass = no "error" severity violations. Warnings are informational only."""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + [
            {
                "name": "read_file",
                "description": "Read a Swift source file for review.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "list_swift_files",
                "description": "List all .swift files in an app project.",
                "input_schema": {
                    "type": "object",
                    "properties": {"app_name": {"type": "string"}},
                    "required": ["app_name"],
                },
            },
            {
                "name": "grep_pattern",
                "description": "Search for a pattern across Swift files. Use for bulk vibe-code detection.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern":  {"type": "string", "description": "grep regex pattern"},
                        "app_name": {"type": "string"},
                    },
                    "required": ["pattern", "app_name"],
                },
            },
        ] + AD4M_TOOL_DEFS

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "read_file":
            return self._read_file(tool_input["path"])
        if tool_name == "list_swift_files":
            return self._list_swift_files(tool_input["app_name"])
        if tool_name == "grep_pattern":
            return self._grep_pattern(tool_input["pattern"], tool_input["app_name"])
        if tool_name.startswith("ad4m_"):
            return execute_ad4m_tool(tool_name, tool_input)
        return super().execute_tool(tool_name, tool_input)

    def _read_file(self, path: str) -> str:
        try:
            content = Path(path).read_text(encoding="utf-8")
            return json.dumps({"content": content[:5000], "truncated": len(content) > 5000})
        except FileNotFoundError:
            return json.dumps({"error": f"Not found: {path}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _list_swift_files(self, app_name: str) -> str:
        base = PROJECTS_DIR / app_name
        if not base.exists():
            return json.dumps({"error": f"Project not found: {base}"})
        files = [str(f) for f in sorted(base.rglob("*.swift")) if ".build" not in str(f)]
        return json.dumps({"app_name": app_name, "swift_files": files, "count": len(files)})

    def _grep_pattern(self, pattern: str, app_name: str) -> str:
        base = PROJECTS_DIR / app_name
        try:
            result = subprocess.run(
                ["grep", "-rn", "--include=*.swift", pattern, str(base)],
                capture_output=True, text=True, timeout=30,
            )
            matches = result.stdout.strip().splitlines()
            return json.dumps({"pattern": pattern, "matches": matches[:50], "count": len(matches)})
        except Exception as e:
            return json.dumps({"error": str(e)})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=str, required=True)
    args = parser.parse_args()

    agent = DesignReviewAgent(name="Design Review Agent")
    result = agent.run(f"Review all Swift files in the {args.app} project against the quality checklist.")
    print("=" * 60)
    print(result["output"])
