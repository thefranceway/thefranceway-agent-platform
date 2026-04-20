#!/usr/bin/env python3
"""
Coasys Watch Scheduler — runs CoasysWatcherAgent every 6 hours.

Monitors: Coasys Medium, AD4M GitHub releases, @lucksus, @ad4m_layer, @coasys
High-signal items (releases, agent posts by Lal) → Telegram DM to owner.

Managed by launchd: com.thefranceway.coasys-watcher.plist
"""

import json
import logging
import os
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

PLATFORM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLATFORM_DIR))

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_PATH = PLATFORM_DIR / "logs" / "coasys_watcher.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger("coasys_watcher")

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "8712606232:AAFuiGeNS6FvDdBpsaweRFvELGfthtTkt7A")
OWNER_CHAT_ID = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")  # Set in launchd env or ~/.zshrc
BOT_API      = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _send_dm(text: str) -> bool:
    """Send a Telegram DM to the owner."""
    if not OWNER_CHAT_ID:
        log.warning("TELEGRAM_OWNER_CHAT_ID not set — skipping DM")
        return False
    try:
        payload = json.dumps({
            "chat_id":    OWNER_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"{BOT_API}/sendMessage",
            data    = payload,
            headers = {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            result = json.loads(resp.read())
        ok = result.get("ok", False)
        if not ok:
            log.error(f"Telegram API error: {result}")
        return ok
    except Exception as e:
        log.error(f"DM send failed: {e}")
        return False


def _format_alert(alert: dict) -> str:
    cat_emoji = {
        "release":    "🚀",
        "agent_post": "🤖",
        "integration": "🔗",
        "research":   "🔬",
        "other":      "📌",
    }
    emoji = cat_emoji.get(alert.get("category", "other"), "📌")
    return (
        f"{emoji} <b>[Coasys Watch]</b> {alert['title']}\n"
        f"<i>{alert.get('summary', '')}</i>\n"
        f"Source: {alert.get('source', '')}\n"
        f"→ {alert.get('link', '')}"
    )


def main():
    log.info("Coasys Watch Scheduler — starting run")

    try:
        from agents.coasys_watcher_agent import CoasysWatcherAgent
        agent  = CoasysWatcherAgent()
        alerts = agent.run_watch()
    except Exception as e:
        log.error(f"Agent failed: {e}", exc_info=True)
        sys.exit(1)

    if not alerts:
        log.info("No new items — nothing to report")
        return

    log.info(f"Agent produced {len(alerts)} alerts")

    dm_sent = 0
    for alert in alerts:
        category = alert.get("category", "other")
        log.info(f"  [{category.upper()}] {alert.get('title', '')[:80]}")

        if alert.get("telegram_dm"):
            msg = _format_alert(alert)
            ok  = _send_dm(msg)
            if ok:
                dm_sent += 1
                log.info(f"    → DM sent")
            else:
                log.warning(f"    → DM failed")

    log.info(f"Run complete — {len(alerts)} alerts, {dm_sent} DMs sent")


if __name__ == "__main__":
    main()
