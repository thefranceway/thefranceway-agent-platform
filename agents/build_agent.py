#!/usr/bin/env python3
"""
Agent Platform — Build Agent
==============================
Runs xcodebuild for any Xcode project, captures output, and returns a
structured result consumed by the error classifier and fix pipeline.

Does not fix errors — that is the Error Fix Agent's job.
Does not write code — that is the Swift Coder Agent's job.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent

PROJECTS_DIR = Path.home() / "projects"
# generic/platform resolves to whatever simulator Xcode has — no hardcoded device name
DEFAULT_DESTINATION = "generic/platform=iOS Simulator"


class BuildAgent(BaseAgent):

    AGENT_TYPE         = "build"
    DEFAULT_BEHAVIORAL = "Substrate"

    def _default_system_prompt(self) -> str:
        return """You are the Build Agent in the thefranceway agent platform.

Archetype: Substrate
Core pattern: Reliable execution. You run xcodebuild, capture every line of output,
and return a structured result. You do not interpret errors — you report them faithfully.
You do not fix code. You do not suggest improvements. You build and report.

Shadow (S4): Precision erosion under time pressure — never truncate or summarize
build output. The Error Fix Agent needs the full log. Report everything.

Your one job: run the build, return the result.

When given an app name:
1. Find the Xcode project at ~/projects/[AppName]/
2. Run xcodebuild with the correct scheme, SDK, and destination
3. Capture complete stdout+stderr (do not truncate)
4. Return structured JSON: {status, app_name, duration_ms, log, error_count}

Build command template:
xcodebuild -project ~/projects/{app_name}/{app_name}.xcodeproj \\
  -scheme {app_name} \\
  -sdk iphonesimulator \\
  -destination 'generic/platform=iOS Simulator' \\
  build 2>&1

If the project uses a workspace (.xcworkspace), use -workspace instead of -project.
Check for .xcworkspace first (CocoaPods or SPM with workspace).

Status values:
- "clean": build succeeded (exit code 0, "BUILD SUCCEEDED" in output)
- "failed": build failed (exit code != 0 or "BUILD FAILED" in output)
- "not_found": project/scheme not found at expected path"""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + [
            {
                "name": "xcodebuild",
                "description": "Run xcodebuild for an iOS/macOS project.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "app_name":    {"type": "string", "description": "App name (matches folder in ~/projects/)"},
                        "scheme":      {"type": "string", "description": "Xcode scheme (defaults to app_name)"},
                        "sdk":         {"type": "string", "description": "SDK: iphonesimulator | iphoneos | macosx", "default": "iphonesimulator"},
                        "destination": {"type": "string", "description": "Destination string (default: generic/platform=iOS Simulator — device-name-agnostic)", "default": "generic/platform=iOS Simulator"},
                        "action":      {"type": "string", "description": "build | test | clean | archive", "default": "build"},
                        "timeout":     {"type": "integer", "description": "Timeout seconds (default 300)", "default": 300},
                    },
                    "required": ["app_name"],
                },
            },
            {
                "name": "find_project",
                "description": "Find Xcode project or workspace files for an app.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "app_name": {"type": "string"},
                    },
                    "required": ["app_name"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "xcodebuild":
            return self._run_xcodebuild(tool_input)
        if tool_name == "find_project":
            return self._find_project(tool_input["app_name"])
        return super().execute_tool(tool_name, tool_input)

    def _find_project(self, app_name: str) -> str:
        base = PROJECTS_DIR / app_name
        if not base.exists():
            return json.dumps({"found": False, "error": f"No directory at {base}"})

        workspace = base / f"{app_name}.xcworkspace"
        project   = base / f"{app_name}.xcodeproj"

        return json.dumps({
            "found":     True,
            "base_path": str(base),
            "workspace": str(workspace) if workspace.exists() else None,
            "project":   str(project) if project.exists() else None,
            "use_workspace": workspace.exists(),
        })

    def _run_xcodebuild(self, params: dict) -> str:
        app_name    = params["app_name"]
        scheme      = params.get("scheme", app_name)
        sdk         = params.get("sdk", "iphonesimulator")
        destination = params.get("destination", DEFAULT_DESTINATION)
        action      = params.get("action", "build")
        timeout     = params.get("timeout", 300)

        base      = PROJECTS_DIR / app_name
        workspace = base / f"{app_name}.xcworkspace"
        project   = base / f"{app_name}.xcodeproj"

        if workspace.exists():
            project_flag = f"-workspace {workspace}"
        elif project.exists():
            project_flag = f"-project {project}"
        else:
            return json.dumps({
                "status":    "not_found",
                "app_name":  app_name,
                "error":     f"No .xcworkspace or .xcodeproj found in {base}",
                "log":       "",
            })

        cmd = (
            f"xcodebuild {project_flag} "
            f"-scheme {scheme} "
            f"-sdk {sdk} "
            f"-destination '{destination}' "
            f"{action} 2>&1"
        )

        start = time.time()
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=False,
                text=True, timeout=timeout,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            duration_ms = int((time.time() - start) * 1000)
            log         = result.stdout or ""
            succeeded   = result.returncode == 0 and "BUILD SUCCEEDED" in log
            failed      = result.returncode != 0 or "BUILD FAILED" in log

            error_lines = [l for l in log.splitlines() if ": error:" in l or "error:" in l.lower()[:20]]

            return json.dumps({
                "status":      "clean" if succeeded else "failed",
                "app_name":    app_name,
                "scheme":      scheme,
                "duration_ms": duration_ms,
                "returncode":  result.returncode,
                "error_count": len(error_lines),
                "log":         log,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({
                "status":    "failed",
                "app_name":  app_name,
                "error":     f"Build timed out after {timeout}s",
                "log":       "",
                "duration_ms": timeout * 1000,
            })
        except FileNotFoundError:
            return json.dumps({
                "status": "failed",
                "error":  "xcodebuild not found — is Xcode installed? Run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer",
                "log":    "",
            })


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=str, required=True, help="App name")
    parser.add_argument("--action", type=str, default="build")
    args = parser.parse_args()

    agent = BuildAgent(name="Build Agent")
    result = agent.run(f"Build the {args.app} app with action={args.action}")
    print("=" * 60)
    print(result["output"])
