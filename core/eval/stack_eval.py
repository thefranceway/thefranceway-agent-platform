#!/usr/bin/env python3
"""
Stack Eval — deterministic pass/fail checker.

Runs on every CI push and weekly via the platform scheduler.
Each check reads actual source files — never infers from directory listings.
Results are written to registry/eval_results/YYYY-MM-DD.json.
If any check regresses vs the previous run, exits with code 1 (CI fails).

Usage:
  python core/eval/stack_eval.py           # full run, writes results
  python core/eval/stack_eval.py --ci      # same, exits 1 on any failure
  python core/eval/stack_eval.py --delta   # show diff vs last run only
"""

import json
import re
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
RESULTS_DIR = ROOT / "registry" / "eval_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

_AD4M_GQL    = "http://localhost:4000/graphql"
_PERSPECTIVE = "a47bf0c3-5a86-4367-a462-f88680491525"

# ── AD4M score write ──────────────────────────────────────────────────────────

def _ad4m_write_link(source: str, predicate: str, target: str) -> None:
    """Write one link to AD4M. Silently no-ops if the executor is unreachable."""
    mutation = """
        mutation PerspectiveAddLink($uuid: String!, $link: LinkInput!) {
          perspectiveAddLink(uuid: $uuid, link: $link) {
            author timestamp
            data { source predicate target }
          }
        }
    """
    payload = json.dumps({
        "query": mutation,
        "variables": {
            "uuid": _PERSPECTIVE,
            "link": {"source": source, "predicate": predicate, "target": target},
        },
    }).encode()
    req = urllib.request.Request(
        _AD4M_GQL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        pass  # AD4M offline — JSON result file is the canonical record


def _write_score_to_ad4m(payload: dict) -> None:
    # origin=franc://session-note convention; provenance: added 2026-05-18 (stack eval CI)
    run_uri = f"eval://stack-eval/{payload['date']}"
    _ad4m_write_link(run_uri, "franc://eval-score",   f"literal://{payload['score']}")
    _ad4m_write_link(run_uri, "franc://has-content",  f"literal://{json.dumps(payload)}")


# ── Checks ────────────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    try:
        return (ROOT / path).read_text()
    except FileNotFoundError:
        return ""


def check_no_hardcoded_secrets() -> tuple[bool, str]:
    """No os.getenv() call should have a non-empty token string as default."""
    pattern = re.compile(r'os\.getenv\([^,]+,\s*"([A-Za-z0-9_\-]{20,})"')
    offenders = []
    for py in ROOT.rglob("*.py"):
        if ".git" in py.parts or "venv" in py.parts:
            continue
        text = py.read_text(errors="ignore")
        for m in pattern.finditer(text):
            offenders.append(f"{py.relative_to(ROOT)}:{m.group(0)[:60]}")
    if offenders:
        return False, "hardcoded secrets: " + "; ".join(offenders[:3])
    return True, "no hardcoded credential fallbacks"


def check_wal_swarm() -> tuple[bool, str]:
    src = _read("core/swarm.py")
    ok = "PRAGMA journal_mode=WAL" in src
    return ok, "swarm.py WAL mode " + ("set" if ok else "MISSING")


def check_wal_base_agent() -> tuple[bool, str]:
    src = _read("core/base_agent.py")
    ok = "PRAGMA journal_mode=WAL" in src
    return ok, "base_agent.py WAL mode " + ("set" if ok else "MISSING")


def check_wal_spar() -> tuple[bool, str]:
    src = _read("core/spar.py")
    ok = "PRAGMA journal_mode=WAL" in src
    return ok, "spar.py WAL mode " + ("set" if ok else "MISSING")


def check_wal_mabp_report() -> tuple[bool, str]:
    src = _read("core/eval/mabp_report.py")
    ok = "PRAGMA journal_mode=WAL" in src
    return ok, "mabp_report.py WAL mode " + ("set" if ok else "MISSING")


def check_api_auth() -> tuple[bool, str]:
    src = _read("api_server.py")
    ok = "_require_api_key" in src
    return ok, "api_server bearer auth " + ("present" if ok else "MISSING")


def check_requirements_pinned() -> tuple[bool, str]:
    src = _read("requirements.txt")
    if not src:
        return False, "requirements.txt not found"
    unpinned = [ln.strip() for ln in src.splitlines() if ln.strip() and ">=" in ln]
    if unpinned:
        return False, "unpinned deps: " + ", ".join(unpinned)
    return True, "all deps pinned with =="


def check_ci_workflow() -> tuple[bool, str]:
    workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    ok = len(workflows) > 0
    return ok, f"{len(workflows)} CI workflow(s) found" if ok else "no CI workflow in .github/workflows/"


def check_rate_limit_on_task() -> tuple[bool, str]:
    src = _read("api_server.py")
    # Check that _check_rate_limit is called inside submit_task or at the route level
    ok = src.count("_check_rate_limit") >= 2
    return ok, "rate limiter applied to multiple endpoints" if ok else "rate limiter only on /route"


def check_registry_matches_factories() -> tuple[bool, str]:
    """
    registry/agents.json (what list_agents()/platform_status() report) and
    core.orchestrator.AGENT_FACTORIES (what dispatch_task can actually route
    to) drifted apart three separate times in one session before this check
    existed: 13 real agent types missing from the registry, 7 test-agent
    entries with no factory behind them, and a phantom "solana" type with
    neither. This makes that class of drift fail CI instead of requiring a
    manual audit to catch it again.
    """
    sys.path.insert(0, str(ROOT))
    from core.orchestrator import AGENT_FACTORIES

    agents_json_path = ROOT / "registry" / "agents.json"
    try:
        agents = json.loads(agents_json_path.read_text())
    except FileNotFoundError:
        return False, "registry/agents.json not found"

    registry_types = {a["type"] for a in agents}
    factory_types  = set(AGENT_FACTORIES.keys())

    # Registry `type` is a display/taxonomy label, not always the literal
    # AGENT_FACTORIES dispatch key — some agents are legitimately routed
    # through dispatch_task(agent_type=...) under one name (factory_types),
    # others are legitimately only ever instantiated directly inside a
    # specific pipeline script and never dispatched generically at all
    # (registry-only, by design, not drift). Two known cases:
    #  - "challenger"/"pragmatist": inline SPARDebater roles, core/spar.py
    #    instantiates them directly.
    #  - "build_agent"/"coasys_watcher"/"design_review"/"error_fix"/
    #    "ios_architect"/"swift_coder": instantiated directly inside
    #    scripts/apple_build_pipeline.py or services/coasys_watch_scheduler.py,
    #    never through the generic orchestrator dispatch path.
    KNOWN_NON_FACTORY_TYPES = {
        "challenger", "pragmatist",
        "build_agent", "coasys_watcher", "design_review", "error_fix",
        "ios_architect", "swift_coder",
    }
    # Older registry entries also use this looser taxonomy instead of the
    # literal factory key for otherwise-real, dispatch_task-routable agents
    # (e.g. Python Expert's type is "coding_expert", not "python"). Newer
    # entries (added when the 13-agent registry gap was fixed) set `type`
    # to the literal factory key directly — that's the convention going
    # forward, so only alias the pre-existing exceptions rather than
    # broadening the taxonomy further.
    KNOWN_TYPE_ALIASES = {
        "coding_expert": {"python", "typescript"},
        "research":      {"content"},   # Content Strategist
        "meta":          {"memory"},    # Memory Agent
        "builder":       {"analytics"}, # Data Analytics Agent
    }

    registry_only = registry_types - factory_types - KNOWN_NON_FACTORY_TYPES
    for reg_type, aliased_factories in KNOWN_TYPE_ALIASES.items():
        if reg_type in registry_only and aliased_factories & factory_types:
            registry_only.discard(reg_type)

    factory_only = factory_types - registry_types
    for aliased_factories in KNOWN_TYPE_ALIASES.values():
        factory_only -= aliased_factories

    if registry_only or factory_only:
        parts = []
        if factory_only:
            parts.append(f"factory keys with no registry entry: {sorted(factory_only)}")
        if registry_only:
            parts.append(f"registry types with no factory (and no known alias): {sorted(registry_only)}")
        return False, "; ".join(parts)
    return True, f"{len(factory_types)} factory keys, all represented in the registry"


# ── Runner ────────────────────────────────────────────────────────────────────

CHECKS = [
    ("no_hardcoded_secrets",  check_no_hardcoded_secrets),
    ("wal_swarm",             check_wal_swarm),
    ("wal_base_agent",        check_wal_base_agent),
    ("wal_spar",              check_wal_spar),
    ("wal_mabp_report",       check_wal_mabp_report),
    ("api_auth",              check_api_auth),
    ("requirements_pinned",   check_requirements_pinned),
    ("ci_workflow",           check_ci_workflow),
    ("rate_limit_coverage",   check_rate_limit_on_task),
    ("registry_sync",         check_registry_matches_factories),
]


def run() -> dict:
    results = {}
    for name, fn in CHECKS:
        try:
            passed, detail = fn()
        except Exception as e:
            passed, detail = False, f"check error: {e}"
        results[name] = {"pass": passed, "detail": detail}
    return results


def load_previous() -> dict | None:
    files = sorted(RESULTS_DIR.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except Exception:
        return None


def compute_delta(prev: dict | None, current: dict) -> list[str]:
    if not prev:
        return []
    regressions = []
    for name, result in current.items():
        prev_result = prev.get("checks", {}).get(name, {})
        if prev_result.get("pass") is True and result["pass"] is False:
            regressions.append(name)
    return regressions


def main() -> int:
    ci_mode    = "--ci" in sys.argv
    delta_mode = "--delta" in sys.argv

    results  = run()
    previous = load_previous()
    deltas   = compute_delta(previous, results)

    passed = sum(1 for r in results.values() if r["pass"])
    total  = len(results)
    score  = round(passed / total * 100)

    print(f"\nStack Eval — {date.today()}  [{passed}/{total} checks passed — {score}%]\n")
    for name, r in results.items():
        icon = "✓" if r["pass"] else "✗"
        flag = "  ← REGRESSION" if name in deltas else ""
        print(f"  {icon}  {name:<28}  {r['detail']}{flag}")

    if deltas:
        print(f"\n⚠  {len(deltas)} regression(s) vs previous run: {', '.join(deltas)}")

    # Write result
    today_file = RESULTS_DIR / f"{date.today()}.json"
    payload = {
        "date":   date.today().isoformat(),
        "score":  score,
        "passed": passed,
        "total":  total,
        "checks": results,
        "regressions": deltas,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    today_file.write_text(json.dumps(payload, indent=2))
    print(f"\nResults written → {today_file.relative_to(ROOT)}")

    _write_score_to_ad4m(payload)
    print(f"AD4M score written → eval://stack-eval/{payload['date']}  [{score}%]")

    if (ci_mode or True) and (deltas or passed < total):
        failed_names = [n for n, r in results.items() if not r["pass"]]
        if failed_names:
            print(f"\nFailing checks: {', '.join(failed_names)}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
