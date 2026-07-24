#!/usr/bin/env python3
"""
Apple Build Pipeline
=====================
General-purpose multi-agent pipeline for building any native iOS/macOS SwiftUI app.

Flow:
  1. Load prior knowledge from AD4M (any known errors/patterns for this app)
  2. iOS Architect Agent → module spec JSON
  3. Swift Coder Agent → writes all source files
  4. Build loop (max 5 iterations):
       Build Agent → Error Classifier → Error Fix Agent → repeat
       Writes error→fix pairs to AD4M each iteration
  5. Design Review Agent → HIG/accessibility/quality gate
  6. Pattern Promoter → check for promotable rules (every 10 runs)

Usage:
  python3 apple_build_pipeline.py --app MyApp --description "..."
  python3 apple_build_pipeline.py --app MyApp --fix-only   # skip architect+coder, just rebuild+fix
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.ios_architect_agent  import iOSArchitectAgent
from agents.swift_coder_agent    import SwiftCoderAgent
from agents.build_agent          import BuildAgent
from agents.error_fix_agent      import ErrorFixAgent
from agents.design_review_agent  import DesignReviewAgent
from agents.error_classifier     import classify, summary as error_summary, to_json
from agents.ad4m_tools           import execute_ad4m_tool

PERSPECTIVE_UUID = "a47bf0c3-5a86-4367-a462-f88680491525"
MAX_BUILD_ITERATIONS = 5
RUN_COUNTER_FILE = Path.home() / ".metaclaw" / "records" / "apple_pipeline_runs.txt"


def _load_ad4m_context(app_name: str) -> str:
    """Load any prior AD4M knowledge about this app."""
    try:
        result = execute_ad4m_tool("ad4m_read_links", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    f"build://app/{app_name}",
            "predicate": None,
        })
        data = json.loads(result)
        if data.get("links"):
            return f"\nPrior AD4M context for {app_name}: {json.dumps(data['links'][:10])}"
    except Exception:
        pass
    return ""


def _write_app_node(app_name: str, description: str) -> None:
    """Register this app in AD4M if not already present."""
    execute_ad4m_tool("ad4m_write_link", {
        "perspective_uuid": PERSPECTIVE_UUID,
        "source":    f"build://app/{app_name}",
        "predicate": "franc://has-content",
        "target":    f"literal://{description[:300]}",
    })
    execute_ad4m_tool("ad4m_write_link", {
        "perspective_uuid": PERSPECTIVE_UUID,
        "source":    "build://stack/apple-agent-stack",
        "predicate": "franc://built-app",
        "target":    f"build://app/{app_name}",
    })


def _write_build_result(app_name: str, attempt: int, status: str, error_count: int) -> None:
    execute_ad4m_tool("ad4m_write_link", {
        "perspective_uuid": PERSPECTIVE_UUID,
        "source":    f"build://app/{app_name}",
        "predicate": "franc://build-attempt",
        "target":    f"literal://attempt={attempt} status={status} errors={error_count}",
    })


def _increment_run_counter() -> int:
    RUN_COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    if RUN_COUNTER_FILE.exists():
        try:
            count = int(RUN_COUNTER_FILE.read_text().strip())
        except ValueError:
            count = 0
    count += 1
    RUN_COUNTER_FILE.write_text(str(count))
    return count


def _load_design_context(design_dir: str) -> dict:
    if not design_dir:
        return None
    dir_path = Path(design_dir.replace("~", str(Path.home())))
    result = {}
    for key, filename in [("prd", "prd.json"), ("ux_spec", "ux_spec.json"), ("design_spec", "design_spec.json")]:
        file = dir_path / filename
        result[key] = file.read_text(encoding="utf-8") if file.exists() else ""
    return result


def run_pipeline(app_name: str, description: str, fix_only: bool = False, design_dir: str = None) -> dict:
    print(f"\n{'='*60}")
    print(f"Apple Build Pipeline — {app_name}")
    print(f"{'='*60}\n")

    start_time = time.time()
    results    = {"app_name": app_name, "phases": {}}

    # ── Load AD4M context ────────────────────────────────────────────────────
    ad4m_context = _load_ad4m_context(app_name)
    _write_app_node(app_name, description)
    print(f"[AD4M] Context loaded: {len(ad4m_context)} chars\n")

    design = _load_design_context(design_dir)
    if design:
        print(f"[Design] Loaded design context from: {design_dir}\n")

    if not fix_only:
        # ── Phase 1: Architecture ────────────────────────────────────────────
        print("[Phase 1] iOS Architect Agent → generating spec...")
        architect = iOSArchitectAgent(name="iOS Architect Agent")
        design_ctx = ""
        if design:
            design_ctx = (
                f"\n\nProduct Requirements (prd.json):\n{design['prd']}"
                f"\n\nUX Architecture (ux_spec.json):\n{design['ux_spec']}"
                f"\n\nDesign Decisions (design_spec.json):\n{design['design_spec']}"
            )
        arch_result = architect.run(
            f"Design the complete SwiftUI architecture for: {description}"
            f"{ad4m_context}{design_ctx}"
        )
        results["phases"]["architect"] = {
            "output":      arch_result["output"][:500],
            "tool_calls":  len(arch_result["tool_calls"]),
            "iterations":  arch_result["iterations"],
        }
        print(f"[Phase 1] Done — {arch_result['iterations']} iterations\n")

        # ── Phase 2: Swift Code Generation ──────────────────────────────────
        print("[Phase 2] Swift Coder Agent → writing source files...")
        coder  = SwiftCoderAgent(name="Swift Coder Agent")
        coder_input = (
            f"Write all Swift/SwiftUI source files for the {app_name} app. "
            f"Architecture spec: {arch_result['output']}"
        )
        coder_result = coder.run(coder_input)
        results["phases"]["coder"] = {
            "tool_calls": len(coder_result["tool_calls"]),
            "iterations": coder_result["iterations"],
        }
        print(f"[Phase 2] Done — {coder_result['iterations']} iterations\n")

    # ── Phase 3: Build Loop ──────────────────────────────────────────────────
    builder  = BuildAgent(name="Build Agent")
    fixer    = ErrorFixAgent(name="Error Fix Agent")
    final_status = "failed"

    for attempt in range(1, MAX_BUILD_ITERATIONS + 1):
        print(f"[Phase 3] Build attempt {attempt}/{MAX_BUILD_ITERATIONS}...")
        build_result = builder.run(f"Build the {app_name} app with action=build")

        # Extract structured result from agent output
        build_data = {}
        try:
            # Agent output may include JSON embedded in text
            import re
            json_match = re.search(r'\{[^{}]*"status"[^{}]*\}', build_result["output"], re.DOTALL)
            if json_match:
                build_data = json.loads(json_match.group())
        except Exception:
            pass

        status      = build_data.get("status", "failed")
        error_count = build_data.get("error_count", 0)
        log         = build_data.get("log", "")

        _write_build_result(app_name, attempt, status, error_count)
        print(f"[Phase 3] Attempt {attempt}: {status} ({error_count} errors)")

        if status == "clean":
            final_status = "clean"
            print(f"[Phase 3] BUILD CLEAN on attempt {attempt}\n")
            break

        if not log:
            print(f"[Phase 3] No build log available — skipping fix\n")
            break

        # Classify and fix errors
        classified = classify(log)
        print(f"[Phase 3] Errors: {error_summary(classified)}")

        if not classified:
            print(f"[Phase 3] No parseable errors — stopping loop\n")
            break

        print(f"[Phase 3] Error Fix Agent → fixing {len(classified)} errors...")
        fix_input = (
            f"Fix these Swift build errors in the {app_name} project. "
            f"Errors: {json.dumps(to_json(classified)[:10])}"
        )
        fixer.run(fix_input)
        print(f"[Phase 3] Fix pass {attempt} complete\n")

    results["phases"]["build_loop"] = {
        "attempts":     attempt,
        "final_status": final_status,
    }

    if final_status != "clean":
        print("[Pipeline] Build did not reach clean state. Review errors above.\n")
        results["pipeline_status"] = "build_failed"
        return results

    # ── Phase 4: Design Review ───────────────────────────────────────────────
    print("[Phase 4] Design Review Agent → quality gate...")
    reviewer     = DesignReviewAgent(name="Design Review Agent")
    design_brief = f"\n\nDesign brief (design_spec.json):\n{design['design_spec']}" if design else ""
    review_result = reviewer.run(
        f"Review all Swift files in the {app_name} project against the quality checklist. "
        f"Return JSON with pass/fail and all violations.{design_brief}"
    )
    results["phases"]["design_review"] = {
        "output":     review_result["output"][:600],
        "iterations": review_result["iterations"],
    }

    # Check if review passed
    review_pass = "pass" not in review_result["output"].lower() or '"pass": true' in review_result["output"]
    print(f"[Phase 4] Design review: {'PASSED' if review_pass else 'FAILED'}\n")

    # ── Phase 5: Pattern Promotion (every 10 runs) ───────────────────────────
    run_count = _increment_run_counter()
    if run_count % 10 == 0:
        print(f"[Phase 5] Run #{run_count} — triggering pattern promotion...")
        import subprocess
        promoter_path = Path(__file__).parent / "promote_swift_rules.py"
        if promoter_path.exists():
            subprocess.run([sys.executable, str(promoter_path)], timeout=60)
        print("[Phase 5] Pattern promotion complete\n")

    # ── Summary ──────────────────────────────────────────────────────────────
    elapsed = int(time.time() - start_time)
    results["pipeline_status"] = "success" if review_pass else "review_failed"
    results["elapsed_seconds"] = elapsed

    print(f"{'='*60}")
    print(f"Pipeline complete — {results['pipeline_status']} ({elapsed}s)")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apple multi-agent build pipeline")
    parser.add_argument("--app",         type=str, required=True,  help="App name (must match ~/projects/[AppName]/ for fix-only)")
    parser.add_argument("--description", type=str, default="",     help="Plain-language app description")
    parser.add_argument("--fix-only",    action="store_true",       help="Skip architect+coder, just rebuild and fix")
    parser.add_argument("--design-dir",  type=str, default=None,    help="Path to design/ directory with prd.json, ux_spec.json, design_spec.json")
    args = parser.parse_args()

    result = run_pipeline(
        app_name    = args.app,
        description = args.description,
        fix_only    = args.fix_only,
        design_dir  = args.design_dir,
    )
    print(json.dumps(result, indent=2))
