#!/usr/bin/env python3
"""
Backend Build Pipeline
======================
Production backend pipeline for any full-stack app.
Parallel to apple_build_pipeline.py — runs independently.

Flow:
  1. Load AD4M context for this app
  2. Backend Architect Agent → spec JSON            [GATE: must return valid JSON]
  3. Database Agent → migrations + seed SQL
  4. API Builder Agent → Hono.js TypeScript project
  5. Infra Agent → wrangler.toml + .env.example
  6. Auth Agent → JWT middleware + auth routes
  7. Security Audit Agent → {critical, warnings}    [GATE: blocked if critical > 0]
  8. Observability Agent → /health + Sentry init
  9. CI/CD Agent → .github/workflows/backend.yml
  10. Write final status to AD4M

Usage:
  python3 backend_build_pipeline.py --app NeuroZip --description "..."
  python3 backend_build_pipeline.py --app NeuroZip --skip-architect
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.backend_architect_agent import BackendArchitectAgent
from agents.database_agent          import DatabaseAgent
from agents.api_builder_agent       import APIBuilderAgent
from agents.auth_agent              import AuthAgent
from agents.infra_agent             import InfraAgent
from agents.security_audit_agent    import SecurityAuditAgent
from agents.ci_cd_agent             import CICDAgent
from agents.observability_agent     import ObservabilityAgent
from agents.ad4m_tools              import execute_ad4m_tool

PERSPECTIVE_UUID = "a47bf0c3-5a86-4367-a462-f88680491525"


def _load_ad4m_context(app_name: str) -> str:
    try:
        result = execute_ad4m_tool("ad4m_read_links", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source": f"build://backend/{app_name}",
            "predicate": None,
        })
        data = json.loads(result)
        if data.get("links"):
            return f"\nPrior AD4M backend context for {app_name}: {json.dumps(data['links'][:10])}"
    except Exception:
        pass
    return ""


def _write_ad4m_node(app_name: str, description: str) -> None:
    try:
        execute_ad4m_tool("ad4m_write_link", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source": f"build://backend/{app_name}",
            "predicate": "franc://has-content",
            "target": f"literal://{description[:300]}",
        })
        execute_ad4m_tool("ad4m_write_link", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source": "build://stack/backend-agent-stack",
            "predicate": "franc://built-backend",
            "target": f"build://backend/{app_name}",
        })
    except Exception:
        pass


def _write_pipeline_status(app_name: str, status: str, critical_count: int) -> None:
    try:
        execute_ad4m_tool("ad4m_write_link", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source": f"build://backend/{app_name}",
            "predicate": "franc://pipeline-status",
            "target": f"literal://status={status} critical_findings={critical_count}",
        })
    except Exception:
        pass


def _load_design_context(design_dir: str) -> dict:
    if not design_dir:
        return None
    dir_path = Path(design_dir.replace("~", str(Path.home())))
    result = {}
    for key, filename in [("prd", "prd.json"), ("ux_spec", "ux_spec.json"), ("design_spec", "design_spec.json")]:
        file = dir_path / filename
        result[key] = file.read_text(encoding="utf-8") if file.exists() else ""
    return result


def _extract_json(text: str) -> dict:
    """Extract first JSON object from agent output text."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def run_pipeline(app_name: str, description: str, skip_architect: bool = False, design_dir: str = None) -> dict:
    print(f"\n{'='*60}")
    print(f"Backend Build Pipeline — {app_name}")
    print(f"{'='*60}\n")

    start_time = time.time()
    results = {"app_name": app_name, "phases": {}}
    backend_dir = Path.home() / "projects" / app_name / "backend"

    # ── Load AD4M context ────────────────────────────────────────────────────
    ad4m_context = _load_ad4m_context(app_name)
    _write_ad4m_node(app_name, description)
    print(f"[AD4M] Context loaded: {len(ad4m_context)} chars\n")

    design = _load_design_context(design_dir)
    if design:
        print(f"[Design] Loaded design context from: {design_dir}\n")

    # ── Phase 1: Architecture ────────────────────────────────────────────────
    spec_json = ""
    if not skip_architect:
        print("[Phase 1] Backend Architect Agent → generating spec...")
        architect = BackendArchitectAgent(name="Backend Architect Agent")
        design_ctx = ""
        if design:
            design_ctx = (
                f"\n\nProduct Requirements (prd.json):\n{design['prd']}"
                f"\n\nUX Architecture — screens and data entities (ux_spec.json):\n{design['ux_spec']}"
            )
        arch_task = (
            f"Design the complete backend architecture for the {app_name} app.\n"
            f"Description: {description}{ad4m_context}\n\n"
            f"Output directory for this project: {backend_dir}{design_ctx}"
        )
        arch_result = architect.run(arch_task)
        results["phases"]["architect"] = {
            "output":      arch_result["output"][:500],
            "tool_calls":  len(arch_result["tool_calls"]),
            "iterations":  arch_result["iterations"],
        }
        spec_json = arch_result["output"]
        print(f"[Phase 1] Done — {arch_result['iterations']} iterations\n")

        # Gate: spec must contain valid JSON
        spec_data = _extract_json(spec_json)
        if not spec_data or "tables" not in spec_data:
            print("[Phase 1] GATE FAIL — architect did not return a valid spec JSON with 'tables'")
            results["pipeline_status"] = "architect_failed"
            _write_pipeline_status(app_name, "architect_failed", 0)
            return results
    else:
        print("[Phase 1] Skipped (--skip-architect) — reusing spec from AD4M context\n")
        spec_json = ad4m_context or f'{{"app_name": "{app_name}", "note": "no spec in AD4M"}}'

    # ── Phase 2a: Database ───────────────────────────────────────────────────
    print("[Phase 2a] Database Agent → writing migrations...")
    db_agent = DatabaseAgent(name="Database Agent")
    data_entities_ctx = ""
    if design and design['ux_spec']:
        data_entities_ctx = f"\n\nData entities required by UX (from ux_spec.json):\n{design['ux_spec']}"
    db_result = db_agent.run(
        f"Write Supabase migration files for the {app_name} backend.\n"
        f"Architecture spec:\n{spec_json}\n\n"
        f"Output directory: {backend_dir}{data_entities_ctx}"
    )
    results["phases"]["database"] = {
        "tool_calls": len(db_result["tool_calls"]),
        "iterations": db_result["iterations"],
    }
    print(f"[Phase 2a] Done — {db_result['iterations']} iterations\n")

    # ── Phase 2b: API Builder ────────────────────────────────────────────────
    print("[Phase 2b] API Builder Agent → writing Hono.js project...")
    api_agent = APIBuilderAgent(name="API Builder Agent")
    api_result = api_agent.run(
        f"Write the complete Hono.js TypeScript project for the {app_name} backend.\n"
        f"Architecture spec:\n{spec_json}\n\n"
        f"Output directory: {backend_dir}"
    )
    results["phases"]["api_builder"] = {
        "tool_calls": len(api_result["tool_calls"]),
        "iterations": api_result["iterations"],
    }
    print(f"[Phase 2b] Done — {api_result['iterations']} iterations\n")

    # ── Phase 2c: Infra ──────────────────────────────────────────────────────
    print("[Phase 2c] Infra Agent → writing wrangler.toml...")
    infra_agent = InfraAgent(name="Infra Agent")
    infra_result = infra_agent.run(
        f"Write wrangler.toml and .env.example for the {app_name} backend.\n"
        f"Architecture spec:\n{spec_json}\n\n"
        f"Output directory: {backend_dir}"
    )
    results["phases"]["infra"] = {
        "tool_calls": len(infra_result["tool_calls"]),
        "iterations": infra_result["iterations"],
    }
    print(f"[Phase 2c] Done — {infra_result['iterations']} iterations\n")

    # ── Phase 3: Auth ────────────────────────────────────────────────────────
    print("[Phase 3] Auth Agent → wiring JWT middleware...")
    auth_agent = AuthAgent(name="Auth Agent")
    auth_result = auth_agent.run(
        f"Write Supabase auth middleware and Apple Sign-In routes for the {app_name} backend.\n"
        f"Architecture spec:\n{spec_json}\n\n"
        f"Source directory: {backend_dir}/src\n"
        f"API builder output summary: {api_result['output'][:800]}"
    )
    results["phases"]["auth"] = {
        "tool_calls": len(auth_result["tool_calls"]),
        "iterations": auth_result["iterations"],
    }
    print(f"[Phase 3] Done — {auth_result['iterations']} iterations\n")

    # ── Phase 4: Security Audit ──────────────────────────────────────────────
    print("[Phase 4] Security Audit Agent → running checks...")
    sec_agent = SecurityAuditAgent(name="Security Audit Agent")
    sec_result = sec_agent.run(
        f"Run security audit on the {app_name} backend.\n"
        f"Backend directory: {backend_dir}\n"
        f"Architecture spec (for cross-reference):\n{spec_json}"
    )
    results["phases"]["security"] = {
        "output":     sec_result["output"],
        "iterations": sec_result["iterations"],
    }

    security_data = _extract_json(sec_result["output"])
    critical_count = security_data.get("critical", 0)
    warning_count  = security_data.get("warnings", 0)
    verdict        = security_data.get("verdict", "unknown")
    results["phases"]["security"].update({
        "critical": critical_count,
        "warnings": warning_count,
        "verdict":  verdict,
    })
    print(f"[Phase 4] Security audit: {verdict.upper()} — {critical_count} critical, {warning_count} warnings\n")

    if critical_count > 0:
        print("[Phase 4] GATE BLOCKED — critical security findings must be resolved:\n")
        for finding in security_data.get("findings", []):
            if finding.get("severity") == "critical":
                print(f"  [{finding.get('check')}] {finding.get('file')}: {finding.get('issue')}")
        print()
        _write_pipeline_status(app_name, "security_blocked", critical_count)
        results["pipeline_status"] = "security_blocked"
        results["elapsed_seconds"] = int(time.time() - start_time)
        return results

    # ── Phase 5: Observability ───────────────────────────────────────────────
    print("[Phase 5] Observability Agent → injecting /health + Sentry...")
    obs_agent = ObservabilityAgent(name="Observability Agent")
    obs_result = obs_agent.run(
        f"Inject Sentry init and /health endpoint into the {app_name} backend.\n"
        f"Source directory: {backend_dir}/src"
    )
    results["phases"]["observability"] = {
        "tool_calls": len(obs_result["tool_calls"]),
        "iterations": obs_result["iterations"],
    }
    print(f"[Phase 5] Done — {obs_result['iterations']} iterations\n")

    # ── Phase 6: CI/CD ───────────────────────────────────────────────────────
    print("[Phase 6] CI/CD Agent → writing GitHub Actions workflow...")
    cicd_agent = CICDAgent(name="CI/CD Agent")
    cicd_result = cicd_agent.run(
        f"Write GitHub Actions CI/CD workflow for the {app_name} backend.\n"
        f"Architecture spec:\n{spec_json}\n\n"
        f"Backend directory: {backend_dir}"
    )
    results["phases"]["ci_cd"] = {
        "tool_calls": len(cicd_result["tool_calls"]),
        "iterations": cicd_result["iterations"],
    }
    print(f"[Phase 6] Done — {cicd_result['iterations']} iterations\n")

    # ── Summary ──────────────────────────────────────────────────────────────
    elapsed = int(time.time() - start_time)
    results["pipeline_status"] = "success"
    results["elapsed_seconds"] = elapsed

    _write_pipeline_status(app_name, "success", 0)

    print(f"{'='*60}")
    print(f"Pipeline complete — success ({elapsed}s)")
    print(f"Backend written to: {backend_dir}")
    print(f"{'='*60}\n")
    print("Next steps (manual — required before deploy):")
    print("  1. Create Supabase project at supabase.com")
    print("  2. Run: wrangler login")
    print("  3. Create KV namespaces and update IDs in wrangler.toml")
    print("  4. Add secrets: wrangler secret put SUPABASE_URL (etc.)")
    print("  5. Add GitHub secrets — see .github/workflows/SECRETS.md")
    print("  6. Push to GitHub — CI/CD runs automatically on PR and main push\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backend multi-agent build pipeline")
    parser.add_argument("--app",            type=str, required=True,       help="App name")
    parser.add_argument("--description",    type=str, default="",          help="Plain-language app description")
    parser.add_argument("--skip-architect", action="store_true",           help="Skip architect, reuse spec from AD4M")
    parser.add_argument("--design-dir",     type=str, default=None,        help="Path to design/ directory with prd.json, ux_spec.json, design_spec.json")
    args = parser.parse_args()

    result = run_pipeline(
        app_name       = args.app,
        description    = args.description,
        skip_architect = args.skip_architect,
        design_dir     = args.design_dir,
    )
    print(json.dumps(result, indent=2))
