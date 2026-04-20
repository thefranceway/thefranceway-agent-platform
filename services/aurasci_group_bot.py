#!/usr/bin/env python3
"""
AuraSci Group Bot — long-polling Bot API service.
Monitors Telegram groups where @thefranceway_bot has been added.
Routes new messages to AuraSciGroupAgent for classification + draft.
Sends draft to owner for review (Mode A — human-in-the-loop).

Run:
    python services/aurasci_group_bot.py

Auto-start: managed by launchd (com.thefranceway.aurasci-group-bot.plist)
Logs: ~/projects/agent-platform/logs/aurasci_group_bot.log

Setup:
    1. Add @thefranceway_bot to your AuraSci Telegram group.
    2. Give it admin (or at least message-read) permissions.
    3. Start this service — it will auto-detect the group on first message.
"""

import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import certifi
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

PLATFORM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLATFORM_DIR))

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_PATH = PLATFORM_DIR / "logs" / "aurasci_group_bot.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger("aurasci_group_bot")

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "8712606232:AAFuiGeNS6FvDdBpsaweRFvELGfthtTkt7A")
BOT_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = 7049234595
POLL_TIMEOUT  = 30      # long-poll seconds
RETRY_SLEEP   = 5       # seconds to wait after network errors

# Monitored group names (bot replies only in groups matching these keywords)
# Empty list = all groups the bot is in
AURASCI_GROUPS: list[str] = ["AuraSci", "aurasci", "aura sci"]

# Persistent dedup — survives restarts
_SEEN_PATH = PLATFORM_DIR / "logs" / "aurasci_seen.json"


def _load_seen() -> set[int]:
    if _SEEN_PATH.exists():
        try:
            return set(json.loads(_SEEN_PATH.read_text()))
        except Exception:
            pass
    return set()


def _save_seen(seen: set[int]):
    ids = sorted(seen)[-5000:]
    _SEEN_PATH.write_text(json.dumps(ids))


_seen: set[int] = _load_seen()

# ── Bot API helpers ────────────────────────────────────────────────────────────

def api_call(method: str, params: dict = None, timeout: int = 35) -> dict:
    url     = f"{BOT_API}/{method}"
    payload = json.dumps(params or {}).encode()
    req     = urllib.request.Request(
        url,
        data    = payload,
        headers = {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read())


def get_updates(offset: int) -> list[dict]:
    try:
        result = api_call("getUpdates", {
            "offset":          offset,
            "timeout":         POLL_TIMEOUT,
            "allowed_updates": ["message"],
        }, timeout=POLL_TIMEOUT + 10)
        return result.get("result", [])
    except (urllib.error.URLError, TimeoutError, Exception) as e:
        log.warning(f"getUpdates error: {e}")
        return []


# ── Message filter ────────────────────────────────────────────────────────────

def is_aurasci_group(chat: dict) -> bool:
    """Return True if the chat is a group/supergroup and matches AuraSci filters."""
    chat_type = chat.get("type", "")
    if chat_type not in ("group", "supergroup"):
        return False

    if not AURASCI_GROUPS:
        return True  # monitor all groups if no filter set

    title = chat.get("title", "").lower()
    return any(kw.lower() in title for kw in AURASCI_GROUPS)


def extract_message(update: dict):
    """
    Returns (sender_name, group_name, text) or None if should skip.
    Skips: bot own messages, commands, non-text, non-AuraSci groups.
    """
    msg = update.get("message")
    if not msg:
        return None

    chat = msg.get("chat", {})
    if not is_aurasci_group(chat):
        return None

    # Skip bot's own messages
    from_user = msg.get("from", {})
    if from_user.get("is_bot"):
        return None

    text = msg.get("text", "").strip()
    if not text:
        return None

    # Skip bot commands
    if text.startswith("/"):
        return None

    # Sender name
    first = from_user.get("first_name", "")
    last  = from_user.get("last_name", "")
    username = from_user.get("username", "")
    sender_name = f"{first} {last}".strip() or "Unknown"
    if username:
        sender_name += f" (@{username})"

    group_name = chat.get("title", "AuraSci")

    return sender_name, group_name, text


# ── Main loop ─────────────────────────────────────────────────────────────────

def process_update(update: dict):
    update_id = update["update_id"]

    if update_id in _seen:
        return
    _seen.add(update_id)
    _save_seen(_seen)

    extracted = extract_message(update)
    if not extracted:
        return

    sender_name, group_name, text = extracted
    log.info(f"Group message | group={group_name} | from={sender_name}")
    log.info(f"Text: {text[:120]}")

    try:
        from agents.aurasci_group_agent import AuraSciGroupAgent
        agent  = AuraSciGroupAgent()
        result = agent.process_message(
            sender = sender_name,
            group  = group_name,
            text   = text,
        )
        log.info(f"Agent output: {result.get('output', '')[:200]}")
    except Exception as e:
        log.error(f"Error processing update {update_id}: {e}", exc_info=True)


def main():
    log.info("Starting AuraSci Group Bot (Mode A — review before posting)")
    log.info(f"Monitoring groups: {AURASCI_GROUPS or 'all groups'}")

    try:
        me = api_call("getMe")["result"]
        log.info(f"Bot: @{me.get('username')} ({me.get('first_name')})")
    except Exception as e:
        log.error(f"Failed to connect to Bot API: {e}")
        sys.exit(1)

    offset = 0
    log.info("Polling for updates...")

    while True:
        updates = get_updates(offset)

        for update in updates:
            process_update(update)
            offset = max(offset, update["update_id"] + 1)

        if not updates:
            # Long-poll returned empty — normal, just loop again
            continue

        # Brief pause between non-empty batches
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("AuraSci group bot stopped by user")
