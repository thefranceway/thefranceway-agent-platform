#!/usr/bin/env python3
"""
Weekly Cost Report — Agent Platform
Reads logs/token_usage.jsonl, calculates spend per agent/model, prints summary.

Usage:
    python scripts/cost_report.py           # last 7 days
    python scripts/cost_report.py --days 30 # last N days
    python scripts/cost_report.py --all     # all time
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

PLATFORM_DIR = Path(__file__).parent.parent
LOG_PATH     = PLATFORM_DIR / "logs" / "token_usage.jsonl"

# Prices per million tokens (as of 2026-03)
PRICING = {
    "claude-sonnet-4-6":         {"in": 3.00,   "out": 15.00},
    "claude-haiku-4-5-20251001": {"in": 0.80,   "out": 4.00},
    "claude-opus-4-6":           {"in": 15.00,  "out": 75.00},
    "gemini-2.0-flash":          {"in": 0.075,  "out": 0.30},
    "llama3.3":                  {"in": 0.0,    "out": 0.0},   # local
}

def cost(model, in_tok, out_tok):
    p = PRICING.get(model, {"in": 3.00, "out": 15.00})
    return (in_tok * p["in"] + out_tok * p["out"]) / 1_000_000

def main():
    days = 7
    all_time = "--all" in sys.argv
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        days = int(sys.argv[idx + 1])

    if not LOG_PATH.exists():
        print("No usage log found yet. Agents will start logging on next run.")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if not all_time else None
    records = []
    with open(LOG_PATH) as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                ts = datetime.fromisoformat(r["ts"])
                if cutoff and ts < cutoff:
                    continue
                records.append(r)
            except Exception:
                continue

    if not records:
        label = "all time" if all_time else f"last {days} days"
        print(f"No usage records for {label}.")
        return

    by_agent  = defaultdict(lambda: {"in": 0, "out": 0, "calls": 0, "model": ""})
    by_model  = defaultdict(lambda: {"in": 0, "out": 0, "calls": 0})
    total_in  = total_out = total_cost = 0

    for r in records:
        a = by_agent[r["agent"]]
        a["in"]    += r["in"]
        a["out"]   += r["out"]
        a["calls"] += 1
        a["model"]  = r["model"]

        m = by_model[r["model"]]
        m["in"]    += r["in"]
        m["out"]   += r["out"]
        m["calls"] += 1

        total_in   += r["in"]
        total_out  += r["out"]
        total_cost += cost(r["model"], r["in"], r["out"])

    label = "ALL TIME" if all_time else f"LAST {days} DAYS"
    print(f"\n{'='*56}")
    print(f"  AGENT PLATFORM — COST REPORT ({label})")
    print(f"  {records[0]['ts'][:10]} → {records[-1]['ts'][:10]}")
    print(f"{'='*56}\n")

    print(f"{'AGENT':<30} {'CALLS':>6} {'IN TOK':>10} {'OUT TOK':>10} {'COST':>8}")
    print("-" * 68)
    for name, d in sorted(by_agent.items(), key=lambda x: -cost(x[1]["model"], x[1]["in"], x[1]["out"])):
        c = cost(d["model"], d["in"], d["out"])
        print(f"{name:<30} {d['calls']:>6} {d['in']:>10,} {d['out']:>10,} ${c:>7.4f}")

    print(f"\n{'MODEL':<30} {'CALLS':>6} {'IN TOK':>10} {'OUT TOK':>10} {'COST':>8}")
    print("-" * 68)
    for model, d in sorted(by_model.items(), key=lambda x: -cost(x[0], x[1]["in"], x[1]["out"])):
        c = cost(model, d["in"], d["out"])
        print(f"{model:<30} {d['calls']:>6} {d['in']:>10,} {d['out']:>10,} ${c:>7.4f}")

    print(f"\n{'TOTAL':<30} {len(records):>6} {total_in:>10,} {total_out:>10,} ${total_cost:>7.4f}")
    print(f"{'='*56}\n")

if __name__ == "__main__":
    main()
