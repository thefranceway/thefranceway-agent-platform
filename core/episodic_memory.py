#!/usr/bin/env python3
"""
Episodic Memory — groups agent runs into sessions and consolidates them into a
shared cross-agent knowledge base (kb_shared).

Episode = a group of consecutive runs with no gap > SESSION_GAP_HOURS between them.
Consolidation = summarize each episode into a single doc in kb_shared so all agents
can recall context from sessions they weren't part of.

Usage (standalone):
    python core/episodic_memory.py --build      # show episode list
    python core/episodic_memory.py --consolidate # write to kb_shared
    python core/episodic_memory.py --stats       # summary stats
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PLATFORM_DIR      = Path(__file__).parent.parent
RUNS_PATH         = PLATFORM_DIR / "registry" / "runs.json"
VECTOR_DIR        = PLATFORM_DIR / "registry" / "vector_store"
SESSION_GAP_HOURS = 4
SHARED_KB         = "kb_shared"

sys.path.insert(0, str(PLATFORM_DIR))
from core.base_agent import JSONVectorStore


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def build_episodes(runs: list[dict] = None) -> list[dict]:
    """
    Group runs into session episodes.
    Returns list of episode dicts sorted by start_time.
    """
    if runs is None:
        if not RUNS_PATH.exists():
            return []
        runs = json.loads(RUNS_PATH.read_text())

    if not runs:
        return []

    # Sort by start time
    sorted_runs = sorted(runs, key=lambda r: r.get("started_at", ""))

    episodes   = []
    current    = []
    gap        = timedelta(hours=SESSION_GAP_HOURS)

    for run in sorted_runs:
        if not current:
            current.append(run)
            continue
        last_end = _parse_dt(current[-1].get("ended_at") or current[-1].get("started_at", ""))
        this_start = _parse_dt(run.get("started_at", ""))
        if this_start - last_end > gap:
            episodes.append(_summarize_episode(current, len(episodes) + 1))
            current = []
        current.append(run)

    if current:
        episodes.append(_summarize_episode(current, len(episodes) + 1))

    return episodes


def _summarize_episode(runs: list[dict], episode_num: int) -> dict:
    agents_used  = list({r.get("agent_name", "unknown") for r in runs})
    task_samples = [r.get("task", "")[:120] for r in runs[:3]]
    shadow_codes = [r.get("shadow_events", {}).get("shadow_code") for r in runs
                    if r.get("shadow_events", {}).get("events_detected", 0) > 0]
    start_time   = runs[0].get("started_at", "")
    end_time     = runs[-1].get("ended_at") or runs[-1].get("started_at", "")

    summary_text = (
        f"Episode {episode_num} | {start_time[:10]} | "
        f"{len(runs)} runs | "
        f"Agents: {', '.join(agents_used)} | "
        f"Tasks: {' | '.join(task_samples)}"
    )
    if shadow_codes:
        summary_text += f" | Shadow events: {', '.join(shadow_codes)}"

    return {
        "episode_id":    f"ep_{episode_num:03d}",
        "episode_num":   episode_num,
        "run_count":     len(runs),
        "agents":        agents_used,
        "start_time":    start_time,
        "end_time":      end_time,
        "shadow_codes":  shadow_codes,
        "task_samples":  task_samples,
        "summary_text":  summary_text,
        "run_ids":       [r.get("run_id", "") for r in runs],
    }


def consolidate(episodes: list[dict] = None) -> int:
    """
    Write episode summaries into kb_shared.
    Returns number of episodes written.
    """
    if episodes is None:
        episodes = build_episodes()

    kb = JSONVectorStore(SHARED_KB)

    # Track which episode_ids are already stored to avoid duplicates
    existing_ids = {
        d.get("metadata", {}).get("episode_id")
        for d in kb._data
    }

    written = 0
    for ep in episodes:
        if ep["episode_id"] in existing_ids:
            continue
        kb.add(
            text=ep["summary_text"],
            metadata={
                "episode_id":  ep["episode_id"],
                "agents":      ep["agents"],
                "start_time":  ep["start_time"],
                "end_time":    ep["end_time"],
                "run_count":   ep["run_count"],
                "type":        "episode_summary",
            },
        )
        written += 1

    return written


def get_recent_episodes(n: int = 5) -> list[dict]:
    episodes = build_episodes()
    return sorted(episodes, key=lambda e: e["start_time"], reverse=True)[:n]


class EpisodicMemory:
    """
    Episodic memory layer for the agent platform.
    Groups runs into sessions and consolidates into kb_shared.
    """
    SHARED_KB         = SHARED_KB
    SESSION_GAP_HOURS = SESSION_GAP_HOURS

    def build_episodes(self) -> list[dict]:
        return build_episodes()

    def consolidate(self) -> int:
        return consolidate()

    def get_recent_episodes(self, n: int = 5) -> list[dict]:
        return get_recent_episodes(n)

    def search_shared(self, query: str, n: int = 5) -> list[dict]:
        kb = JSONVectorStore(SHARED_KB)
        return kb.search(query, n_results=n)


# ── Patch BaseAgent.recall() to also search kb_shared ────────────────────────

def _patch_base_agent_recall():
    """
    Monkey-patch BaseAgent.recall() to merge results from the agent's own KB
    and kb_shared (cross-agent episodic context).
    """
    try:
        from core.base_agent import BaseAgent, JSONVectorStore as JVS

        _original_recall = BaseAgent.recall

        def _patched_recall(self, query: str, n: int = 5) -> list[dict]:
            own_results    = _original_recall(self, query, n)
            shared_kb      = JVS(SHARED_KB)
            shared_results = shared_kb.search(query, n_results=n)

            # Merge — deduplicate by id, keep top-n by score
            seen = {r["id"] for r in own_results}
            merged = list(own_results)
            for r in shared_results:
                if r["id"] not in seen:
                    merged.append(r)
                    seen.add(r["id"])

            merged.sort(key=lambda x: x["score"], reverse=True)
            return merged[:n]

        BaseAgent.recall = _patched_recall
    except Exception:
        pass  # patch is best-effort, never break the agent


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Episodic memory management")
    parser.add_argument("--build",       action="store_true", help="Show episode list")
    parser.add_argument("--consolidate", action="store_true", help="Write episodes to kb_shared")
    parser.add_argument("--stats",       action="store_true", help="Summary stats")
    parser.add_argument("--recent",      type=int, default=5, help="Show N most recent episodes")
    args = parser.parse_args()

    if args.consolidate:
        n = consolidate()
        print(f"Consolidated {n} new episodes into kb_shared")

    elif args.build:
        episodes = build_episodes()
        print(f"\n{len(episodes)} episodes found:\n")
        for ep in episodes:
            agents = ", ".join(ep["agents"][:3])
            print(f"  {ep['episode_id']}  {ep['start_time'][:16]}  {ep['run_count']:2d} runs  [{agents}]")
        print()

    elif args.stats:
        episodes  = build_episodes()
        kb        = JSONVectorStore(SHARED_KB)
        print(f"\nEpisodes:          {len(episodes)}")
        print(f"kb_shared docs:    {kb.count()}")
        if episodes:
            total_runs  = sum(e["run_count"] for e in episodes)
            avg_runs    = total_runs / len(episodes)
            print(f"Total runs:        {total_runs}")
            print(f"Avg runs/episode:  {avg_runs:.1f}")
            print(f"Date range:        {episodes[0]['start_time'][:10]} → {episodes[-1]['end_time'][:10]}")
        print()

    else:
        recent = get_recent_episodes(args.recent)
        print(f"\nMost recent {len(recent)} episodes:\n")
        for ep in recent:
            print(f"  {ep['episode_id']}  {ep['start_time'][:16]}  {ep['run_count']} runs")
            for t in ep["task_samples"][:2]:
                print(f"    • {t[:80]}")
        print()
