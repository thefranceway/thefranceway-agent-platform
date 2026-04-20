#!/usr/bin/env python3
"""
DeSci News Scheduler — posts 2-3 daily DeSci news items to the AuraSci group.

Runs once daily via launchd (com.thefranceway.desci-news.plist).
Uses DeSciNewsAgent to fetch + curate news, then posts via Bot API.

Requires @thefranceway_bot to be an Admin in the AuraSci group.
"""

import json
import logging
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

import certifi
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

PLATFORM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLATFORM_DIR))

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_PATH = PLATFORM_DIR / "logs" / "desci_news.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger("desci_news")

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "REDACTED-TELEGRAM-BOT-TOKEN")
BOT_API         = f"https://api.telegram.org/bot{BOT_TOKEN}"
AURASCI_CHAT_ID = -1002125539398   # AuraSci main group
POST_DELAY      = 30               # seconds between posts (avoid flooding)


def send_message(text: str) -> bool:
    """Post a message to the AuraSci group via Bot API."""
    try:
        payload = json.dumps({
            "chat_id":    AURASCI_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
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
        log.error(f"Failed to send message: {e}")
        return False


def main():
    log.info("DeSci News Scheduler — starting daily run")

    try:
        from agents.desci_news_agent import DeSciNewsAgent
        agent = DeSciNewsAgent()
        posts = agent.run_daily()
    except Exception as e:
        log.error(f"Agent failed: {e}", exc_info=True)
        sys.exit(1)

    if not posts:
        log.warning("No posts generated — skipping")
        return

    log.info(f"Agent produced {len(posts)} posts")

    # Header message
    header = "🔬 <b>DeSci Daily</b> — top stories for the AuraSci community\n"
    send_message(header)
    time.sleep(5)

    # Post each news item with a delay
    for i, post in enumerate(posts, 1):
        log.info(f"Posting item {i}: {post[:80]}...")
        ok = send_message(post)
        if ok:
            log.info(f"Item {i} posted")
        else:
            log.error(f"Item {i} failed")
        if i < len(posts):
            time.sleep(POST_DELAY)

    log.info("Daily run complete")


if __name__ == "__main__":
    main()
