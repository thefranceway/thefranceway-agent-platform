#!/usr/bin/env python3
"""
Weekly Cost Report — Agent Platform
Reads from SQLite cost_ledger (preferred) or logs/token_usage.jsonl (fallback).

Usage:
    python scripts/cost_report.py              # last 7 days, SQLite if available
    python scripts/cost_report.py --days 30    # last N days
    python scripts/cost_report.py --all        # all time
    python scripts/cost_report.py --agent "Builder Agent"  # filter by agent name
    python scripts/cost_report.py --jsonl      # force JSONL source
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

PLATFORM_DIR = Path(__file__).parent.parent
LOG_PATH     = PLATFORM_DIR / "logs" / "token_usage.jsonl"
DB_PATH      = PLATFORM_DIR / "registry" / "agent_platform.db"

PRICING = {
    "claude-sonnet-4-6":         {"in": 3.00,   "out": 15.00},
    "claude-haiku-4-5-20251001": {"in": 0.80,   "out": 4.00},
    "claude-opus-4-6":           {"in": 15.00,  "out": 75.00},
    "claude-opus-4-7":           {"in": 15.00,  "out": 75.00},
    "gemini-2.0-flash":          {"in": 0.075,  "out": 0.30},
    "llama3.3":                  {"in": 0.0,    "out": 0.0},
}


def rate_cost(model: str, in_tok: int, out_tok: int) -> float:
    p = PRICING.get(model, {"in": 3.00, "out": 15.00})
    return (in_tok * p["in"] + out_tok * p["out"]) / 1_000_000


def load_sqlite(cutoff: datetime, agent_filter: str = None) -> list[dict]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("SELECT 1 FROM cost_ledger LIMIT 1")
    except sqlite3.OperationalError:
        conn.close()
        return []

    query  = "SELECT * FROM cost_ledger"
    params = []
    conds  = []
    if cutoff:
        conds.append("timestamp >= ?")
        params.append(cutoff.isoformat())
    if agent_filter:
        conds.append("agent_name LIKE ?")
        params.append(f"%{agent_filter}%")
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += " ORDER BY timestamp ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [
        {
            "ts":    r["timestamp"],
            "agent": r["agent_name"] or "",
            "model": r["model"] or "",
            "in":    r["input_tokens"],
            "out":   r["output_tokens"],
        }
        for r in rows
    ]


def load_jsonl(cutoff: datetime, agent_filter: str = None) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    records = []
    with open(LOG_PATH) as f:
        for line in f:
            try:
                r  = json.loads(line.strip())
                ts = datetime.fromisoformat(r["ts"])
                if cutoff and ts < cutoff:
                    continue
                if agent_filter and agent_filter.lower() not in r.get("agent", "").lower():
                    continue
                records.append(r)
            except Exception:
                continue
    return records


def print_report(records: list[dict], label: str) -> None:
    if not records:
        print(f"No usage records for {label}.")
        return

    by_agent = defaultdict(lambda: {"in": 0, "out": 0, "calls": 0, "model": ""})
    by_model = defaultdict(lambda: {"in": 0, "out": 0, "calls": 0})
    total_in = total_out = total_cost = 0

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
        total_cost += rate_cost(r["model"], r["in"], r["out"])

    print(f"\n{'='*68}")
    print(f"  AGENT PLATFORM — COST REPORT ({label})")
    print(f"  {records[0]['ts'][:10]} → {records[-1]['ts'][:10]}")
    print(f"{'='*68}\n")

    print(f"{'AGENT':<34} {'CALLS':>6} {'IN':>10} {'OUT':>10} {'COST':>8}")
    print("-" * 72)
    for name, d in sorted(by_agent.items(), key=lambda x: -rate_cost(x[1]["model"], x[1]["in"], x[1]["out"])):
        c = rate_cost(d["model"], d["in"], d["out"])
        print(f"{name:<34} {d['calls']:>6} {d['in']:>10,} {d['out']:>10,} ${c:>7.4f}")

    print(f"\n{'MODEL':<34} {'CALLS':>6} {'IN':>10} {'OUT':>10} {'COST':>8}")
    print("-" * 72)
    for model, d in sorted(by_model.items(), key=lambda x: -rate_cost(x[0], x[1]["in"], x[1]["out"])):
        c = rate_cost(model, d["in"], d["out"])
        print(f"{model:<34} {d['calls']:>6} {d['in']:>10,} {d['out']:>10,} ${c:>7.4f}")

    print(f"\n{'TOTAL':<34} {len(records):>6} {total_in:>10,} {total_out:>10,} ${total_cost:>7.4f}")
    print(f"{'='*68}\n")


def main():
    parser = argparse.ArgumentParser(description="Agent Platform cost report")
    parser.add_argument("--days",   type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument("--all",    action="store_true",  help="All-time report")
    parser.add_argument("--agent",  type=str, default=None, help="Filter by agent name (partial match)")
    parser.add_argument("--jsonl",  action="store_true",  help="Force JSONL source instead of SQLite")
    args = parser.parse_args()

    cutoff = None if args.all else datetime.now(timezone.utc) - timedelta(days=args.days)
    label  = "ALL TIME" if args.all else f"LAST {args.days} DAYS"
    if args.agent:
        label += f" — agent: {args.agent}"

    if args.jsonl:
        records = load_jsonl(cutoff, args.agent)
        source  = "JSONL"
    else:
        records = load_sqlite(cutoff, args.agent)
        source  = "SQLite"
        if not records:
            records = load_jsonl(cutoff, args.agent)
            source  = "JSONL (SQLite empty)"

    print(f"[source: {source}]", end="")
    print_report(records, label)


if __name__ == "__main__":
    main()
