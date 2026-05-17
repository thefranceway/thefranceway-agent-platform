#!/usr/bin/env python3
"""
MABP Outcome Report
===================
Queries mabp_outcomes to surface routing accuracy and shadow pattern frequency.

Usage:
    python -m core.eval.mabp_report             # all reports
    python -m core.eval.mabp_report --routing   # routing accuracy by layer
    python -m core.eval.mabp_report --shadows   # shadow pattern frequency
    python -m core.eval.mabp_report --recalibrate
"""

import argparse
import json
import sqlite3
from pathlib import Path

PLATFORM_DIR = Path(__file__).parent.parent.parent
DB_PATH      = PLATFORM_DIR / "registry" / "agent_platform.db"
CONTROL_FILE = PLATFORM_DIR / "core" / "runtime" / "control_state.json"
MIN_SAMPLES  = 20


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def routing_accuracy_by_layer():
    conn = get_db()
    rows = conn.execute("""
        SELECT
            routing_layer,
            COUNT(*)               AS total,
            AVG(outcome_score)     AS avg_score,
            SUM(had_error)         AS errors,
            AVG(routing_confidence) AS avg_conf
        FROM mabp_outcomes
        GROUP BY routing_layer
        ORDER BY avg_score DESC
    """).fetchall()
    conn.close()

    if not rows:
        print("No MABP outcomes recorded yet — run some tasks first.")
        return

    print(f"\n{'Layer':<12} {'Tasks':>6} {'Avg Score':>10} {'Errors':>7} {'Err %':>7} {'Avg Conf':>9}")
    print("-" * 57)
    for r in rows:
        total   = r["total"]
        errors  = r["errors"] or 0
        err_pct = (errors / total * 100) if total else 0
        print(
            f"{(r['routing_layer'] or 'unknown'):<12}"
            f"{total:>6}"
            f"{r['avg_score']:>10.1f}"
            f"{errors:>7}"
            f"{err_pct:>7.1f}%"
            f"{(r['avg_conf'] or 0):>9.2f}"
        )
    print()


def shadow_frequency():
    conn = get_db()
    rows = conn.execute("""
        SELECT agent_type, shadow_events
        FROM mabp_outcomes
        WHERE shadow_count > 0
    """).fetchall()
    conn.close()

    if not rows:
        print("No shadow events recorded yet.")
        return

    code_counts:  dict[str, int]       = {}
    agent_counts: dict[str, dict[str, int]] = {}

    for row in rows:
        agent  = row["agent_type"] or "unknown"
        events = []
        try:
            events = json.loads(row["shadow_events"] or "[]")
        except Exception:
            pass
        for event in events:
            code = event.get("code", "?")
            code_counts[code] = code_counts.get(code, 0) + 1
            agent_counts.setdefault(agent, {})
            agent_counts[agent][code] = agent_counts[agent].get(code, 0) + 1

    print("\nShadow Pattern Frequency:")
    print(f"  {'Code':<6} {'Count':>6}")
    print("  " + "-" * 14)
    for code, count in sorted(code_counts.items(), key=lambda x: -x[1]):
        print(f"  {code:<6} {count:>6}")

    print("\nBy Agent Type:")
    for agent, codes in sorted(agent_counts.items()):
        parts = ", ".join(f"{c}×{n}" for c, n in sorted(codes.items(), key=lambda x: -x[1]))
        print(f"  {agent:<22} {parts}")
    print()


def recalibrate_confidence():
    conn = get_db()
    rows = conn.execute("""
        SELECT routing_layer, COUNT(*) AS total, AVG(outcome_score) AS avg_score
        FROM mabp_outcomes
        GROUP BY routing_layer
    """).fetchall()
    conn.close()

    if not CONTROL_FILE.exists():
        print(f"control_state.json not found at {CONTROL_FILE}")
        return

    with open(CONTROL_FILE) as f:
        state = json.load(f)

    updated = False
    for row in rows:
        layer = row["routing_layer"]
        total = row["total"]
        score = row["avg_score"] or 0

        if total < MIN_SAMPLES:
            print(f"  {layer}: {total} samples — need {MIN_SAMPLES} to recalibrate, skipping")
            continue

        # Map empirical score (0-100) → confidence (0.50-0.99)
        new_conf = round(max(0.50, min(0.99, score / 100)), 2)
        key      = f"mabp_confidence_{layer}"
        old_conf = state.get(key, "not set")
        state[key] = new_conf
        updated = True
        print(f"  {layer}: score={score:.1f} over {total} tasks → {old_conf} → {new_conf}")

    if updated:
        with open(CONTROL_FILE, "w") as f:
            json.dump(state, f, indent=2)
        print(f"\nWritten to {CONTROL_FILE}")
        print("get_param() reads these on next dispatch.")
    else:
        print("No layers had enough samples for recalibration.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MABP Outcome Report")
    parser.add_argument("--routing",     action="store_true", help="Routing accuracy by layer")
    parser.add_argument("--shadows",     action="store_true", help="Shadow pattern frequency")
    parser.add_argument("--recalibrate", action="store_true", help="Recalibrate confidence values")
    args = parser.parse_args()

    if not any([args.routing, args.shadows, args.recalibrate]):
        routing_accuracy_by_layer()
        shadow_frequency()
    else:
        if args.routing:
            routing_accuracy_by_layer()
        if args.shadows:
            shadow_frequency()
        if args.recalibrate:
            recalibrate_confidence()
