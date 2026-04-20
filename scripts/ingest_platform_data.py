#!/usr/bin/env python3
"""
Platform Data Ingestion — Agent Platform
Parses all platform logs into structured metrics JSON.
No AI required — pure data extraction.

Usage:
    python scripts/ingest_platform_data.py           # print summary
    python scripts/ingest_platform_data.py --json    # print full JSON
    python scripts/ingest_platform_data.py --save    # save to logs/platform_metrics.json
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

PLATFORM_DIR = Path(__file__).parent.parent
LOGS_DIR     = PLATFORM_DIR / "logs"

# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_api_server_log():
    path = LOGS_DIR / "api_server.log"
    if not path.exists():
        return {}

    pattern = re.compile(
        r'INFO:\s+(\S+) - "(\w+) (\S+) HTTP/\S+" (\d+)'
    )
    by_day   = defaultdict(lambda: defaultdict(int))
    by_route = defaultdict(int)
    errors   = defaultdict(int)

    for line in path.read_text().splitlines():
        m = pattern.search(line)
        if not m:
            continue
        ip, method, route, status = m.group(1), m.group(2), m.group(3), int(m.group(4))
        by_route[f"{method} {route}"] += 1
        if status >= 400:
            errors[f"{status} {route}"] += 1

    return {
        "total_requests": sum(by_route.values()),
        "by_route":       dict(sorted(by_route.items(), key=lambda x: -x[1])),
        "errors":         dict(sorted(errors.items(), key=lambda x: -x[1])),
        "error_count":    sum(errors.values()),
    }


def parse_structured_log(path: Path, label: str):
    """Parse logs with format: YYYY-MM-DD HH:MM:SS,ms [LEVEL] message"""
    if not path.exists():
        return {}

    line_re  = re.compile(r'(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] (.+)')
    by_day   = defaultdict(lambda: {"INFO": 0, "WARNING": 0, "ERROR": 0, "DEBUG": 0})
    by_level = defaultdict(int)
    events   = []

    for line in path.read_text().splitlines():
        m = line_re.match(line)
        if not m:
            continue
        date, time, level, msg = m.group(1), m.group(2), m.group(3), m.group(4)
        by_day[date][level] += 1
        by_level[level]     += 1

        # extract meaningful events (not repeated noise)
        if level in ("INFO", "ERROR") and not any(noise in msg for noise in [
            "getUpdates error", "read operation timed out", "Connection to",
            "Connecting to", "Connection closed", "Closing current",
        ]):
            events.append({"date": date, "time": time, "level": level, "msg": msg[:200]})

    # deduplicate events
    seen   = set()
    unique = []
    for e in events:
        key = e["msg"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return {
        "label":        label,
        "total_lines":  sum(sum(d.values()) for d in by_day.values()),
        "by_level":     dict(by_level),
        "by_day":       {k: dict(v) for k, v in sorted(by_day.items())},
        "unique_events": unique[:50],  # cap at 50 unique events
    }


def parse_telegram_log():
    path = LOGS_DIR / "telegram_monitor.log"
    if not path.exists():
        return {}

    line_re  = re.compile(r'(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] (.+)')
    msg_re   = re.compile(r'New message \| type=(\S*) \| from=(.+?) \| chat=(.+)')
    result_re = re.compile(r'(Triggered|Filtered out|Drafted|Sent|Processed)')

    messages    = []
    by_chat     = defaultdict(int)
    by_from     = defaultdict(int)
    outcomes    = defaultdict(int)
    errors      = []

    for line in path.read_text().splitlines():
        m = line_re.match(line)
        if not m:
            continue
        date, time, level, msg = m.group(1), m.group(2), m.group(3), m.group(4)

        mm = msg_re.search(msg)
        if mm:
            sender = mm.group(2)
            chat   = mm.group(3)
            by_chat[chat]   += 1
            by_from[sender] += 1
            messages.append({"date": date, "time": time, "from": sender, "chat": chat})
            continue

        rm = result_re.search(msg)
        if rm:
            outcomes[rm.group(1)] += 1

        if level == "ERROR" and "credit balance" not in msg:
            errors.append({"date": date, "msg": msg[:150]})

    return {
        "total_messages":  len(messages),
        "by_chat":         dict(sorted(by_chat.items(), key=lambda x: -x[1])),
        "by_sender":       dict(sorted(by_from.items(), key=lambda x: -x[1])),
        "outcomes":        dict(outcomes),
        "error_count":     len(errors),
        "recent_messages": messages[-20:],
    }


def parse_token_usage():
    path = LOGS_DIR / "token_usage.jsonl"
    if not path.exists():
        return {"status": "no data yet — agents need Anthropic API credits to log usage"}

    PRICING = {
        "claude-sonnet-4-6":         {"in": 3.00,  "out": 15.00},
        "claude-haiku-4-5-20251001": {"in": 0.80,  "out": 4.00},
        "claude-opus-4-6":           {"in": 15.00, "out": 75.00},
        "gemini-2.0-flash":          {"in": 0.075, "out": 0.30},
    }

    def cost(model, in_tok, out_tok):
        p = PRICING.get(model, {"in": 3.00, "out": 15.00})
        return (in_tok * p["in"] + out_tok * p["out"]) / 1_000_000

    by_agent = defaultdict(lambda: {"in": 0, "out": 0, "calls": 0, "model": "", "cost": 0.0})
    by_model = defaultdict(lambda: {"in": 0, "out": 0, "calls": 0, "cost": 0.0})
    by_day   = defaultdict(lambda: {"calls": 0, "cost": 0.0})
    records  = []

    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                records.append(r)
                c = cost(r["model"], r["in"], r["out"])
                day = r["ts"][:10]

                a = by_agent[r["agent"]]
                a["in"]    += r["in"]
                a["out"]   += r["out"]
                a["calls"] += 1
                a["model"]  = r["model"]
                a["cost"]  += c

                m = by_model[r["model"]]
                m["in"]    += r["in"]
                m["out"]   += r["out"]
                m["calls"] += 1
                m["cost"]  += c

                by_day[day]["calls"] += 1
                by_day[day]["cost"]  += c
            except Exception:
                continue

    total_cost = sum(r["cost"] for r in by_agent.values())
    return {
        "total_calls": len(records),
        "total_cost":  round(total_cost, 4),
        "by_agent":    {k: {**v, "cost": round(v["cost"], 4)} for k, v in
                        sorted(by_agent.items(), key=lambda x: -x[1]["cost"])},
        "by_model":    {k: {**v, "cost": round(v["cost"], 4)} for k, v in
                        sorted(by_model.items(), key=lambda x: -x[1]["cost"])},
        "by_day":      {k: {"calls": v["calls"], "cost": round(v["cost"], 4)}
                        for k, v in sorted(by_day.items())},
        "date_range":  f"{records[0]['ts'][:10]} → {records[-1]['ts'][:10]}" if records else "n/a",
    }


def parse_fingerprints():
    path = LOGS_DIR / "fingerprints.jsonl"
    if not path.exists():
        return {}

    records  = []
    by_key   = defaultdict(int)
    by_day   = defaultdict(int)

    for line in path.read_text().splitlines():
        try:
            r = json.loads(line.strip())
            records.append(r)
            by_key[r["key_hash"]] += 1
            by_day[r["ts"][:10]]  += 1
        except Exception:
            continue

    return {
        "total_api_calls": len(records),
        "unique_keys":     len(by_key),
        "by_day":          dict(sorted(by_day.items())),
        "by_key_hash":     dict(sorted(by_key.items(), key=lambda x: -x[1])),
    }


# ── Health Summary ────────────────────────────────────────────────────────────

def platform_health(data: dict) -> dict:
    issues = []
    status = "healthy"

    if not (LOGS_DIR / "token_usage.jsonl").exists():
        issues.append("token_usage.jsonl missing — Anthropic API credits required")
        status = "degraded"

    tg = data.get("telegram", {})
    if tg.get("error_count", 0) > 10:
        issues.append(f"Telegram monitor: {tg['error_count']} errors logged")

    api = data.get("api_server", {})
    if api.get("error_count", 0) > 0:
        issues.append(f"API server: {api['error_count']} 4xx/5xx responses")

    return {"status": status, "issues": issues}


# ── Main ──────────────────────────────────────────────────────────────────────

def ingest() -> dict:
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_server":   parse_api_server_log(),
        "telegram":     parse_telegram_log(),
        "token_usage":  parse_token_usage(),
        "fingerprints": parse_fingerprints(),
    }
    data["health"] = platform_health(data)
    return data


def print_summary(data: dict):
    print(f"\n{'='*56}")
    print(f"  PLATFORM METRICS — {data['generated_at'][:10]}")
    print(f"{'='*56}")

    h = data["health"]
    print(f"\nHealth: {h['status'].upper()}")
    for issue in h["issues"]:
        print(f"  ⚠  {issue}")

    api = data["api_server"]
    if api:
        print(f"\nAPI Server: {api['total_requests']} total requests, {api['error_count']} errors")
        for route, count in list(api["by_route"].items())[:5]:
            print(f"  {count:>5}  {route}")

    tg = data["telegram"]
    if tg:
        print(f"\nTelegram Monitor: {tg['total_messages']} messages tracked")
        for chat, count in list(tg["by_chat"].items())[:5]:
            print(f"  {count:>4}  {chat}")
        if tg["outcomes"]:
            print(f"  Outcomes: {tg['outcomes']}")

    tok = data["token_usage"]
    if "status" in tok:
        print(f"\nToken Usage: {tok['status']}")
    else:
        print(f"\nToken Usage: {tok['total_calls']} calls, ${tok['total_cost']:.4f} total")
        print(f"  Range: {tok['date_range']}")
        for agent, d in list(tok["by_agent"].items())[:5]:
            print(f"  {agent:<30} ${d['cost']:.4f}")

    fp = data.get("fingerprints", {})
    if fp:
        print(f"\nAPI Fingerprints: {fp['total_api_calls']} calls, {fp['unique_keys']} unique keys")

    print()


if __name__ == "__main__":
    data = ingest()

    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
    elif "--save" in sys.argv:
        out = LOGS_DIR / "platform_metrics.json"
        out.write_text(json.dumps(data, indent=2))
        print(f"Saved to {out}")
        print_summary(data)
    else:
        print_summary(data)
