#!/usr/bin/env python3
"""
Longterm Memory Eval — measures recall accuracy across all agent knowledge bases.

Methodology:
- Load runs.json (94 execution traces)
- For each run where the agent called remember(), treat the original task as
  the query and the stored doc as ground truth
- Call the same KB's search() with that query
- Hit = ground truth doc appears in top-3 results
- Score = hits / total * 100

Usage:
    python scripts/memory_eval.py          # full eval
    python scripts/memory_eval.py --agent "Telegram Inbox Agent"
    python scripts/memory_eval.py --verbose
"""

import json
import sys
import argparse
from pathlib import Path
from collections import defaultdict

PLATFORM_DIR = Path(__file__).parent.parent
RUNS_PATH    = PLATFORM_DIR / "registry" / "runs.json"
VECTOR_DIR   = PLATFORM_DIR / "registry" / "vector_store"

sys.path.insert(0, str(PLATFORM_DIR))
from core.base_agent import JSONVectorStore, EmbeddingStore, DB_PATH, BaseAgent as _BaseAgent

# Minimal agent shim to call _extract_memory_fact without full init
class _FactExtractor:
    def __init__(self, agent_name: str):
        self.name = agent_name
    _extract_memory_fact = _BaseAgent._extract_memory_fact


def load_runs(agent_filter: str = None) -> list[dict]:
    if not RUNS_PATH.exists():
        print("ERROR: runs.json not found")
        sys.exit(1)
    runs = json.loads(RUNS_PATH.read_text())
    if agent_filter:
        runs = [r for r in runs if r.get("agent_name", "").lower() == agent_filter.lower()]
    return runs


def get_remember_calls(run: dict) -> list[dict]:
    """Extract all remember() tool calls from a run."""
    return [
        tc for tc in run.get("tool_calls", [])
        if tc.get("tool") == "remember"
    ]


def get_kb_name(run: dict) -> str:
    """Derive KB collection name from agent name."""
    agent_name = run.get("agent_name", "")
    name_map = {
        "Ops Agent":                "kb_ops",
        "Builder Agent":            "kb_builder",
        "Content Strategist":       "kb_content_strategist",
        "Longevity Research Agent": "kb_longevity",
        "Memory Agent":             "kb_memory_agent",
        "Data Analytics Agent":     "kb_data_analytics",
        "Meta Agent":               "kb_meta",
        "Solana Expert":            "kb_solana",
        "TypeScript Expert":        "kb_typescript",
        "Python Expert":            "kb_python",
        "Life Coach Agent":         "kb_coaching",
        "Work Coach Agent":         "kb_coaching",
    }
    return name_map.get(agent_name, f"kb_{agent_name.lower().replace(' ', '_')}")


def classify_miss(run: dict, kb: JSONVectorStore, query: str, ground_truth_id: str) -> str:
    """Classify why a recall missed."""
    # Check if doc is in the KB at all
    all_docs = kb._data
    stored_ids = {d["id"] for d in all_docs}
    if ground_truth_id not in stored_ids:
        return "not_stored"

    # Check if it's marked superseded
    doc = next((d for d in all_docs if d["id"] == ground_truth_id), None)
    if doc and doc.get("metadata", {}).get("superseded_by"):
        return "temporal"

    # It's stored but didn't surface — low score
    return "low_score"


def _extract_unique_query(run: dict) -> str:
    """
    Extract the most discriminative part of a task as the recall query.
    Strips boilerplate prefixes that are identical across all runs of the same agent.
    """
    task       = run.get("task", "")
    agent_name = run.get("agent_name", "")
    lines      = task.splitlines()

    # Telegram Inbox: skip "New Telegram message:\nFrom: X\nChat: Y" header, use message body
    if "Telegram" in agent_name:
        body_lines = [l for l in lines if l.strip() and
                      not l.startswith("New Telegram message") and
                      not l.startswith("From:") and
                      not l.startswith("Chat:") and
                      not l.startswith("Message ID:") and
                      not l.startswith("Date:")]
        if body_lines:
            return " ".join(body_lines)[:400]

    # Default: use full task, skip first line if it's a generic header
    if lines and len(lines[0]) < 50 and lines[0].endswith(":"):
        return " ".join(lines[1:])[:400]

    return task[:400]


SKIP_AGENTS: set = set()


def run_eval(agent_filter: str = None, verbose: bool = False, simulate_new_format: bool = False) -> dict:
    runs = [r for r in load_runs(agent_filter) if r.get("agent_name") not in SKIP_AGENTS]

    total_runs     = len(runs)
    evaluated      = 0   # runs that had remember() calls
    hits           = 0
    misses         = 0
    failures       = defaultdict(int)
    miss_details   = []

    # Cache KBs to avoid reloading
    kb_cache:  dict[str, JSONVectorStore] = {}
    emb_cache: dict[str, EmbeddingStore]  = {}

    for run in runs:
        remember_calls = get_remember_calls(run)
        if not remember_calls:
            failures["not_stored"] += 1
            continue

        kb_name = get_kb_name(run)
        if kb_name not in kb_cache:
            kb_path = VECTOR_DIR / f"{kb_name}.json"
            if not kb_path.exists():
                failures["not_stored"] += len(remember_calls)
                continue
            kb_cache[kb_name]  = JSONVectorStore(kb_name)
            emb_store          = EmbeddingStore(kb_name, DB_PATH)
            emb_store._migrated = True   # already migrated, skip auto-migrate overhead
            emb_cache[kb_name] = emb_store

        kb  = kb_cache[kb_name]
        emb = emb_cache.get(kb_name)
        query = _extract_unique_query(run)

        extractor = _FactExtractor(run.get("agent_name", ""))

        for tc in remember_calls:
            evaluated += 1
            stored_text = tc.get("input", {}).get("text", "")
            if not stored_text:
                failures["not_stored"] += 1
                continue

            result_str = tc.get("result", "{}")
            try:
                result_obj = json.loads(result_str)
                ground_truth_id = result_obj.get("id")
            except Exception:
                ground_truth_id = None

            if simulate_new_format:
                # Build an in-memory KB using new-format facts from all runs of this agent
                sim_kb = JSONVectorStore.__new__(JSONVectorStore)
                sim_kb.collection = kb_name + "_sim"
                sim_kb._data = []
                for r2 in runs:
                    if r2.get("agent_name") != run.get("agent_name"):
                        continue
                    for tc2 in r2.get("tool_calls", []):
                        if tc2.get("tool") != "remember":
                            continue
                        try:
                            rid = json.loads(tc2.get("result", "{}")).get("id")
                        except Exception:
                            rid = None
                        if not rid:
                            continue
                        new_text = extractor._extract_memory_fact(
                            r2.get("task", ""), r2.get("output", "")
                        )
                        sim_kb._data.append({
                            "id":       rid,
                            "text":     new_text,
                            "tokens":   kb._tokenize(new_text),
                            "metadata": {},
                            "added_at": r2.get("started_at", ""),
                        })
                results = sim_kb.search(query, n_results=3)
            else:
                results = kb.search(query, n_results=3)

            result_ids = {r["id"] for r in results}

            if ground_truth_id and ground_truth_id in result_ids:
                hits += 1
                if verbose:
                    print(f"  HIT  [{run['agent_name'][:25]}] {query[:60]}")
            else:
                misses += 1
                category = classify_miss(run, kb, query, ground_truth_id) if ground_truth_id else "not_stored"
                failures[category] += 1
                if verbose:
                    print(f"  MISS [{run['agent_name'][:25]}] ({category}) {query[:60]}")
                miss_details.append({
                    "agent":    run.get("agent_name"),
                    "category": category,
                    "query":    query[:80],
                })

    score = round(hits / evaluated * 100, 1) if evaluated > 0 else 0.0

    return {
        "score":        score,
        "hits":         hits,
        "misses":       misses,
        "evaluated":    evaluated,
        "total_runs":   total_runs,
        "failures":     dict(failures),
        "miss_details": miss_details,
    }


def print_report(result: dict):
    print()
    print("=" * 50)
    print("  LONGTERM MEMORY EVAL")
    print("=" * 50)
    print(f"  Overall:    {result['score']}%  ({result['hits']}/{result['evaluated']} hits)")
    print()
    print("  Failure breakdown:")
    for category, count in sorted(result["failures"].items(), key=lambda x: -x[1]):
        label = {
            "not_stored":  "not stored     — agent never called remember()",
            "low_score":   "low score      — stored but didn't surface in top-3",
            "temporal":    "superseded     — stored but marked as outdated",
            "cross_agent": "cross-agent    — stored in wrong KB",
        }.get(category, category)
        print(f"    {label}: {count}")
    print()
    print(f"  Total runs inspected: {result['total_runs']}")
    print(f"  Runs with remember(): {result['evaluated']}")
    print("=" * 50)
    print()

    # Grade
    score = result["score"]
    if score >= 87.5:
        print(f"  BEATS baseline (87.5%) — score: {score}%")
    elif score >= 75:
        print(f"  CLOSE — {87.5 - score:.1f}pts below target (87.5%)")
    else:
        print(f"  NEEDS WORK — {87.5 - score:.1f}pts below target (87.5%)")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run longterm memory eval")
    parser.add_argument("--agent",    type=str, help="Filter to single agent name")
    parser.add_argument("--verbose",  action="store_true", help="Show hit/miss per run")
    parser.add_argument("--json",     action="store_true", help="Output raw JSON")
    parser.add_argument("--simulate", action="store_true", help="Simulate new structured storage format")
    args = parser.parse_args()

    result = run_eval(agent_filter=args.agent, verbose=args.verbose, simulate_new_format=args.simulate)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)
