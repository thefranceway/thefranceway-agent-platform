#!/usr/bin/env python3
"""
Product Design Pipeline — Layer 0
====================================
Pre-pipeline phase that runs before apple_build_pipeline.py or backend_build_pipeline.py.
Converts a plain-language description into three structured design artifacts:
  - prd.json         — MoSCoW-prioritized feature spec
  - ux_spec.json     — Screen inventory, navigation graph, data entities
  - design_spec.json — Typography, colors, components, interaction patterns

Usage:
  python3 scripts/product_design_pipeline.py --app AppName --description "..."
  python3 scripts/product_design_pipeline.py --app AppName --description "..." --platform ios|backend|both
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.product_architect_agent import ProductArchitectAgent
from agents.ux_architecture_agent   import UXArchitectureAgent
from agents.design_decisions_agent  import DesignDecisionsAgent
from agents.ad4m_tools              import execute_ad4m_tool

PERSPECTIVE_UUID = "a47bf0c3-5a86-4367-a462-f88680491525"


def _load_ad4m_context(app_name: str) -> str:
    try:
        result = execute_ad4m_tool("ad4m_read_links", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source": f"build://design/{app_name}",
            "predicate": None,
        })
        data = json.loads(result)
        if data.get("links"):
            return f"\nPrior AD4M design context for {app_name}: {json.dumps(data['links'][:10])}"
    except Exception:
        pass
    return ""


def _write_design_links(app_name: str) -> None:
    for predicate, target in [
        ("franc://has-prd",         "literal://prd.json written"),
        ("franc://has-ux-spec",     "literal://ux_spec.json written"),
        ("franc://has-design-spec", "literal://design_spec.json written"),
    ]:
        try:
            execute_ad4m_tool("ad4m_write_link", {
                "perspective_uuid": PERSPECTIVE_UUID,
                "source":    f"build://design/{app_name}",
                "predicate": predicate,
                "target":    target,
            })
        except Exception:
            pass


def run_pipeline(app_name: str, description: str, platform: str = None) -> dict:
    print(f"\n{'='*60}")
    print(f"Product Design Pipeline — {app_name}")
    print(f"{'='*60}\n")

    start_time = time.time()
    results    = {"app_name": app_name, "phases": {}}
    design_dir = Path.home() / "projects" / app_name / "design"

    ad4m_context = _load_ad4m_context(app_name)
    print(f"[AD4M] Design context loaded: {len(ad4m_context)} chars\n")

    # ── Phase 0a: Product Requirements ──────────────────────────────────────
    print("[Phase 0a] Product Architect Agent → generating PRD...")
    prd_agent = ProductArchitectAgent(name="Product Architect Agent")
    prd_task = (
        f"Generate a complete PRD for the {app_name} app.\n"
        f"Description: {description}{ad4m_context}\n\n"
        f"Write the PRD JSON to: ~/projects/{app_name}/design/prd.json"
    )
    prd_result = prd_agent.run(prd_task)
    results["phases"]["prd"] = {
        "output":     prd_result["output"][:500],
        "tool_calls": len(prd_result["tool_calls"]),
        "iterations": prd_result["iterations"],
    }

    if '"features"' not in prd_result["output"]:
        print("[Phase 0a] GATE FAIL — PRD output does not contain 'features' key")
        results["pipeline_status"] = "prd_failed"
        return results

    prd_content = prd_result["output"]
    prd_file = design_dir / "prd.json"
    if prd_file.exists():
        prd_content = prd_file.read_text(encoding="utf-8")

    print(f"[Phase 0a] Done — {prd_result['iterations']} iterations\n")

    # ── Phase 0b: UX Architecture ────────────────────────────────────────────
    print("[Phase 0b] UX Architecture Agent → generating UX spec...")
    ux_agent = UXArchitectureAgent(name="UX Architecture Agent")
    ux_task = (
        f"Generate a complete UX spec for the {app_name} app.\n"
        f"Product Requirements (prd.json):\n{prd_content}\n\n"
        f"Write the UX spec JSON to: ~/projects/{app_name}/design/ux_spec.json"
    )
    ux_result = ux_agent.run(ux_task)
    results["phases"]["ux_spec"] = {
        "output":     ux_result["output"][:500],
        "tool_calls": len(ux_result["tool_calls"]),
        "iterations": ux_result["iterations"],
    }

    ux_output = ux_result["output"]
    if '"screens"' not in ux_output or '"data_entities"' not in ux_output:
        print("[Phase 0b] GATE FAIL — UX spec missing 'screens' or 'data_entities'")
        results["pipeline_status"] = "ux_spec_failed"
        return results

    ux_content = ux_result["output"]
    ux_file = design_dir / "ux_spec.json"
    if ux_file.exists():
        ux_content = ux_file.read_text(encoding="utf-8")

    print(f"[Phase 0b] Done — {ux_result['iterations']} iterations\n")

    # ── Phase 0c: Design Decisions ───────────────────────────────────────────
    print("[Phase 0c] Design Decisions Agent → generating design spec...")
    design_agent = DesignDecisionsAgent(name="Design Decisions Agent")
    design_task = (
        f"Generate a complete design spec for the {app_name} app.\n"
        f"Product Requirements (prd.json):\n{prd_content}\n\n"
        f"UX Architecture (ux_spec.json):\n{ux_content}\n\n"
        f"Write the design spec JSON to: ~/projects/{app_name}/design/design_spec.json"
    )
    design_result = design_agent.run(design_task)
    results["phases"]["design_spec"] = {
        "output":     design_result["output"][:500],
        "tool_calls": len(design_result["tool_calls"]),
        "iterations": design_result["iterations"],
    }

    if '"components"' not in design_result["output"]:
        print("[Phase 0c] GATE FAIL — Design spec missing 'components' key")
        results["pipeline_status"] = "design_spec_failed"
        return results

    print(f"[Phase 0c] Done — {design_result['iterations']} iterations\n")

    # ── Write AD4M design links ───────────────────────────────────────────────
    _write_design_links(app_name)

    # ── Summary ──────────────────────────────────────────────────────────────
    elapsed = int(time.time() - start_time)
    results["pipeline_status"] = "success"
    results["elapsed_seconds"] = elapsed
    results["design_dir"]      = str(design_dir)

    print(f"{'='*60}")
    print(f"Design pipeline complete — success ({elapsed}s)")
    print(f"Design artifacts written to: {design_dir}")
    print(f"{'='*60}\n")

    if platform == "backend":
        next_cmd = f"python3 scripts/backend_build_pipeline.py --app {app_name} --description \"...\" --design-dir ~/projects/{app_name}/design/"
    elif platform == "both":
        next_cmd = (
            f"python3 scripts/apple_build_pipeline.py --app {app_name} --description \"...\" --design-dir ~/projects/{app_name}/design/\n"
            f"  python3 scripts/backend_build_pipeline.py --app {app_name} --description \"...\" --design-dir ~/projects/{app_name}/design/"
        )
    else:
        next_cmd = f"python3 scripts/apple_build_pipeline.py --app {app_name} --description \"...\" --design-dir ~/projects/{app_name}/design/"

    print(f"Design pipeline complete. Run pipelines with --design-dir ~/projects/{app_name}/design/")
    print(f"\nNext command(s):\n  {next_cmd}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Product Design Pipeline — Layer 0")
    parser.add_argument("--app",         type=str, required=True,  help="App name")
    parser.add_argument("--description", type=str, required=True,  help="Plain-language app description")
    parser.add_argument("--platform",    type=str, default=None,
                        choices=["ios", "backend", "both"],
                        help="Target platform (used to generate next-step command)")
    args = parser.parse_args()

    result = run_pipeline(
        app_name    = args.app,
        description = args.description,
        platform    = args.platform,
    )
    print(json.dumps(result, indent=2))
