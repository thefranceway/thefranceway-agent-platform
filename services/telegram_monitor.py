#!/usr/bin/env python3
"""
Telegram Monitor Service — background daemon using Telethon.
Listens for new messages and routes them:
  - DMs + group mentions  → TelegramInboxAgent
  - AuraSci group (all)   → AuraSciGroupAgent (Mode A — draft for review)

Run:
    python services/telegram_monitor.py

Auto-start: managed by launchd (com.thefranceway.telegram-monitor.plist)
Logs: ~/projects/agent-platform/logs/telegram_monitor.log
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.types import User

PLATFORM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLATFORM_DIR))

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_PATH = PLATFORM_DIR / "logs" / "telegram_monitor.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger("telegram_monitor")

# ── Config ────────────────────────────────────────────────────────────────────

API_ID   = int(os.getenv("TG_APP_ID",   "31273644"))
API_HASH = os.getenv("TG_API_HASH",     "443150c58c345bd44cc6ff366c4ca251")
SESSION  = os.getenv("TG_SESSION_PATH", str(Path.home() / ".telegram-mcp" / "telethon_monitor"))

# ── AuraSci group config ──────────────────────────────────────────────────────

# Exact group titles to monitor (all messages, not just mentions).
# Set at startup — edit this list to control which groups are watched.
# If empty, falls back to keyword matching on AURASCI_KEYWORDS.
AURASCI_EXACT_TITLES: list[str] = ["AuraSci", "AuraSci Cores\U0001f9d8"]

# Fallback keyword matching (case-insensitive substring) used when
# AURASCI_EXACT_TITLES is empty.
AURASCI_KEYWORDS: list[str] = ["AuraSci", "aurasci", "aura sci"]

# Runtime: chat_id → chat_title, populated at startup from dialogs
_aurasci_chat_ids: dict[int, str] = {}

# ── Persistent dedup ──────────────────────────────────────────────────────────

_SEEN_PATH = PLATFORM_DIR / "logs" / "telegram_seen.json"


def _load_seen() -> set[str]:
    if _SEEN_PATH.exists():
        try:
            return set(json.loads(_SEEN_PATH.read_text()))
        except Exception:
            pass
    return set()


def _save_seen(seen: set[str]):
    ids = sorted(seen)[-2000:]
    _SEEN_PATH.write_text(json.dumps(ids))


_seen: set[str] = _load_seen()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_aurasci_group(chat_id: int) -> bool:
    return chat_id in _aurasci_chat_ids


def _sender_name(sender) -> str:
    if isinstance(sender, User):
        parts = [sender.first_name or "", sender.last_name or ""]
        name  = " ".join(p for p in parts if p).strip()
        if sender.username:
            name += f" (@{sender.username})"
        return name or "Unknown"
    return "Unknown"


def _chat_name(chat) -> str:
    if hasattr(chat, "title") and chat.title:
        return chat.title
    if hasattr(chat, "username") and chat.username:
        return f"@{chat.username}"
    return "DM"

# ── Main handler ──────────────────────────────────────────────────────────────

async def handle_message(event, client):
    seen_key = f"{event.chat_id}:{event.message.id}"
    if seen_key in _seen:
        return
    _seen.add(seen_key)
    _save_seen(_seen)

    # Skip own outgoing messages
    if event.out:
        return

    try:
        sender    = await event.get_sender()
        chat      = await event.get_chat()

        # Skip bots — prevents processing our own bot's DM notifications (loops)
        if getattr(sender, "bot", False):
            return

        name      = _sender_name(sender)
        chat_name = _chat_name(chat)
        text      = event.message.message or ""

        if not text:
            return

        # ── Route: AuraSci group (mentions/questions/keywords only) ─────────
        if event.is_group and _is_aurasci_group(event.chat_id):
            TRIGGER_KEYWORDS = [
                "thefranceway", "francesca", "aurasci", "desci", "franc token",
                "partnership", "collaborate", "who runs", "who is",
            ]
            text_lower = text.lower()
            is_mention = getattr(event.message, "mentioned", False)
            is_question = "?" in text
            is_keyword = any(kw in text_lower for kw in TRIGGER_KEYWORDS)

            if not (is_mention or is_question or is_keyword):
                log.debug(f"AuraSci group | skipped (no trigger) | from={name}")
                return

            log.info(f"AuraSci group | TRIGGERED | mention={is_mention} q={is_question} kw={is_keyword} | from={name}")
            log.info(f"Text: {text[:100]}")
            from agents.aurasci_group_agent import AuraSciGroupAgent
            agent  = AuraSciGroupAgent()
            result = agent.process_message(sender=name, group=chat_name, text=text)
            reply  = agent.get_reply()
            if reply:
                await event.reply(reply)
                log.info(f"AuraSci reply posted: {reply[:120]}")
            else:
                log.info("AuraSci: classified FYI/IGNORE — no reply")
            return

        # ── Route: DM ─────────────────────────────────────────────────────────
        if event.is_private:
            log.info(f"DM | from={name} | INBOX AGENT DISABLED — skipping")
            return

        # ── Route: Other group — only if mentioned ────────────────────────────
        if event.is_group and event.message.mentioned:
            log.info(f"Group mention | from={name} | chat={chat_name} | INBOX AGENT DISABLED — skipping")
            return

        # Everything else: silent skip

    except Exception as e:
        log.error(f"Error handling message {seen_key}: {e}", exc_info=True)

# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    log.info("Starting Telegram Monitor Service")
    log.info(f"Session: {SESSION}")

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()

    me = await client.get_me()
    log.info(f"Logged in as: {me.first_name} (@{me.username}) — ID {me.id}")

    # Preload dialogs → populates Telethon's entity cache (required to receive
    # events from all groups) and builds the AuraSci chat ID index.
    dialogs = await client.get_dialogs()
    log.info(f"Dialogs loaded: {len(dialogs)} total")

    for d in dialogs:
        if not (d.is_group or d.is_channel):
            continue
        title = d.name or ""
        match = False
        if AURASCI_EXACT_TITLES:
            match = title in AURASCI_EXACT_TITLES
        else:
            match = any(kw.lower() in title.lower() for kw in AURASCI_KEYWORDS)
        if match:
            _aurasci_chat_ids[d.id] = title

    if _aurasci_chat_ids:
        log.info(f"Monitoring AuraSci groups ({len(_aurasci_chat_ids)}): "
                 + ", ".join(_aurasci_chat_ids.values()))
    else:
        log.warning("No AuraSci groups matched — check AURASCI_EXACT_TITLES config")

    @client.on(events.NewMessage)
    async def on_message(event):
        await handle_message(event, client)

    log.info("Listening for new messages... (Ctrl+C to stop)")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Monitor stopped by user")
