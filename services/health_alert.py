#!/usr/bin/env python3
"""
Health Alert — checks GET /status locally, DMs the owner on failure.

Run on an interval (e.g. every 5 minutes) to catch outages like the
MetaClaw-proxy incident and the /status NameError fixed earlier in this
branch — before a paying RapidAPI customer notices and complains, not after.

Not installed automatically — no launchd/launchctl access from this
session. To install it, save the following as
~/Library/LaunchAgents/com.thefranceway.health-alert.plist (fill in the
absolute path and your actual TELEGRAM_* values) and run
`launchctl load ~/Library/LaunchAgents/com.thefranceway.health-alert.plist`:

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
      <key>Label</key><string>com.thefranceway.health-alert</string>
      <key>ProgramArguments</key>
      <array>
        <string>/usr/bin/env</string>
        <string>python3</string>
        <string>/Users/multiuniverse/projects/agent-platform/services/health_alert.py</string>
      </array>
      <key>EnvironmentVariables</key>
      <dict>
        <key>TELEGRAM_BOT_TOKEN</key><string>YOUR_BOT_TOKEN</string>
        <key>TELEGRAM_OWNER_CHAT_ID</key><string>YOUR_CHAT_ID</string>
      </dict>
      <key>StartInterval</key><integer>300</integer>
      <key>RunAtLoad</key><true/>
    </dict>
    </plist>
"""

import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

PLATFORM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLATFORM_DIR))

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_PATH = PLATFORM_DIR / "logs" / "health_alert.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(message)s",
    handlers = [logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger("health_alert")

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL      = os.getenv("PLATFORM_BASE_URL", "http://localhost:8788")
STATUS_URL    = f"{BASE_URL}/status"
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")
BOT_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Don't re-alert on every single check during a prolonged outage — once,
# then again only after this many minutes of continued failure.
ALERT_COOLDOWN_MINUTES = 30

STATE_PATH = PLATFORM_DIR / "logs" / "health_alert_state.json"

EXPECTED_STATUS_KEYS = {"platform", "registry", "queue"}


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


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"alerting": False, "last_alert_ts": None}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"alerting": False, "last_alert_ts": None}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def check_status() -> tuple[bool, str]:
    """Returns (healthy, reason). reason is empty when healthy."""
    try:
        req = urllib.request.Request(STATUS_URL)
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            body = json.loads(resp.read())
    except urllib.error.URLError as e:
        return False, f"connection failed: {e}"
    except Exception as e:
        return False, f"unexpected error: {e}"

    missing = EXPECTED_STATUS_KEYS - set(body.keys())
    if missing:
        return False, f"response missing expected keys: {missing}"

    return True, ""


def main():
    healthy, reason = check_status()
    state = _load_state()
    now = datetime.now(timezone.utc)

    if healthy:
        log.info("Healthy")
        if state.get("alerting"):
            _send_dm(f"✅ <b>[Health Alert]</b> {BASE_URL} recovered.")
            _save_state({"alerting": False, "last_alert_ts": None})
        return

    log.warning(f"Unhealthy: {reason}")

    last_alert_ts = state.get("last_alert_ts")
    should_alert = True
    if last_alert_ts:
        elapsed_minutes = (now - datetime.fromisoformat(last_alert_ts)).total_seconds() / 60
        should_alert = elapsed_minutes >= ALERT_COOLDOWN_MINUTES

    if should_alert:
        sent = _send_dm(
            f"🔴 <b>[Health Alert]</b> {BASE_URL} is unhealthy.\n"
            f"Reason: {reason}\n"
            f"Time: {now.isoformat()}"
        )
        if sent:
            _save_state({"alerting": True, "last_alert_ts": now.isoformat()})
    else:
        log.info(f"Already alerted {last_alert_ts} — within cooldown, skipping DM")


if __name__ == "__main__":
    main()
