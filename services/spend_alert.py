#!/usr/bin/env python3
"""
Spend Alert — reads token_usage.jsonl, calculates today's cost,
sends Telegram DM if over threshold OR as daily digest at 9pm.

Run modes:
  python spend_alert.py --check    # alert only if over threshold (run frequently)
  python spend_alert.py --digest   # always send summary (run once daily)

Managed by launchd: com.thefranceway.spend-alert
  --check  every 30 min
  --digest daily at 9pm
"""

import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import certifi

_SSL = ssl.create_default_context(cafile=certifi.where())

PLATFORM_DIR  = Path(__file__).parent.parent
LOG_PATH      = PLATFORM_DIR / "logs" / "token_usage.jsonl"
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "8712606232:AAFuiGeNS6FvDdBpsaweRFvELGfthtTkt7A")
OWNER_CHAT_ID = os.getenv("TELEGRAM_OWNER_CHAT_ID", "7049234595")
BOT_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"

DAILY_ALERT_THRESHOLD = 2.00   # $ — send warning if today's spend exceeds this

PRICING = {
    "claude-sonnet-4-6":         {"in": 3.00,  "out": 15.00},
    "claude-haiku-4-5-20251001": {"in": 0.80,  "out": 4.00},
    "claude-opus-4-6":           {"in": 15.00, "out": 75.00},
    "gemini-2.0-flash":          {"in": 0.075, "out": 0.30},
}

def _cost(model: str, in_tok: int, out_tok: int) -> float:
    p = PRICING.get(model, {"in": 3.00, "out": 15.00})
    return (in_tok * p["in"] + out_tok * p["out"]) / 1_000_000

def _send(text: str) -> None:
    payload = json.dumps({
        "chat_id":    OWNER_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"{BOT_API}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10, context=_SSL)
    except Exception as e:
        print(f"Telegram send failed: {e}")

def _read_today() -> tuple[float, dict, int]:
    """Returns (total_cost, cost_by_agent, record_count) for today UTC."""
    if not LOG_PATH.exists():
        return 0.0, {}, 0

    today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total    = 0.0
    by_agent = {}
    count    = 0

    with open(LOG_PATH) as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                if not r.get("ts", "").startswith(today):
                    continue
                c = _cost(r.get("model", ""), r.get("in", 0), r.get("out", 0))
                total += c
                agent = r.get("agent", "unknown")
                by_agent[agent] = by_agent.get(agent, 0) + c
                count += 1
            except Exception:
                continue

    return total, by_agent, count

def main():
    mode = "--digest" if "--digest" in sys.argv else "--check"
    total, by_agent, count = _read_today()

    if mode == "--check":
        if total < DAILY_ALERT_THRESHOLD:
            return  # Under threshold, stay quiet
        lines = [f"⚠️ <b>Spend alert</b> — ${total:.4f} today (threshold ${DAILY_ALERT_THRESHOLD:.2f})"]
        for agent, cost in sorted(by_agent.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {agent}: ${cost:.4f}")
        lines.append(f"\n<i>{count} API calls today</i>")
        _send("\n".join(lines))

    else:  # --digest
        if count == 0:
            _send("💰 <b>Spend digest</b> — no API calls logged today.")
            return
        lines = [f"💰 <b>Spend digest</b> — ${total:.4f} today ({count} calls)"]
        for agent, cost in sorted(by_agent.items(), key=lambda x: -x[1]):
            lines.append(f"  {agent}: ${cost:.4f}")
        lines.append(f"\n<i>Alert threshold: ${DAILY_ALERT_THRESHOLD:.2f}/day</i>")
        _send("\n".join(lines))

if __name__ == "__main__":
    main()
