#!/usr/bin/env python3
"""
Git Content Daemon — watches thefranceway GitHub repos for merged PRs and new tags,
drafts tweets via GitContentAgent, sends to Telegram for approval, posts on approval.

Flow:
  merged PR / new tag detected
    → draft_tweet() via git_content_agent.py
    → send draft to Telegram with ✅ / ❌ inline keyboard
    → on next run: check getUpdates for callbacks
    → ✅ → post to Twitter via OAuth 1.0a
    → ❌ → discard silently

Limits:
  - Max 2 posts/day
  - Quiet hours: 10pm – 8am (no new drafts sent, approval still processed)
  - Skip noise PR titles (fix, wip, typo, etc.)

State files:
  registry/git_content_state.json   — last seen PR/tag per repo + daily count
  registry/git_content_pending.json — awaiting Telegram approval

Managed by launchd: com.thefranceway.git-content-daemon (every 5 min)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

PLATFORM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLATFORM_DIR))

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_PATH = PLATFORM_DIR / "logs" / "git_content_daemon.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger("git_content")

# ── Config ────────────────────────────────────────────────────────────────────

GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "REDACTED-GITHUB-TOKEN")
BOT_TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN", "REDACTED-TELEGRAM-BOT-TOKEN")
OWNER_CHAT_ID     = os.getenv("TELEGRAM_OWNER_CHAT_ID", "7049234595")
BOT_API           = f"https://api.telegram.org/bot{BOT_TOKEN}"

TW_API_KEY        = os.getenv("TWITTER_API_KEY",              "REDACTED-TWITTER-API-KEY")
TW_API_SECRET     = os.getenv("TWITTER_API_SECRET",           "REDACTED-TWITTER-API-SECRET")
TW_ACCESS_TOKEN   = os.getenv("TWITTER_ACCESS_TOKEN",         "REDACTED-TWITTER-ACCESS-TOKEN")
TW_ACCESS_SECRET  = os.getenv("TWITTER_ACCESS_TOKEN_SECRET",  "REDACTED-TWITTER-ACCESS-SECRET")

WATCHED_REPOS     = [
    "thefranceway/franc-token",
    "thefranceway/agent-human-manual",
    "thefranceway/mabp",
    "thefranceway/mcp-video",
    "thefranceway/session-bus",
]

MAX_POSTS_PER_DAY = 2
QUIET_HOUR_START  = 22   # 10pm
QUIET_HOUR_END    = 8    # 8am

STATE_PATH   = PLATFORM_DIR / "registry" / "git_content_state.json"
PENDING_PATH = PLATFORM_DIR / "registry" / "git_content_pending.json"

# ── State helpers ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"repos": {}, "posts_today": 0, "posts_date": "", "tg_offset": 0}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _load_pending() -> list:
    if PENDING_PATH.exists():
        try:
            data = json.loads(PENDING_PATH.read_text())
            return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def _save_pending(pending: list) -> None:
    PENDING_PATH.write_text(json.dumps(pending, indent=2))


def _posts_today(state: dict) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("posts_date") != today:
        state["posts_today"] = 0
        state["posts_date"]  = today
    return state["posts_today"]


def _in_quiet_hours() -> bool:
    h = datetime.now(timezone.utc).hour
    if QUIET_HOUR_START <= QUIET_HOUR_END:
        return QUIET_HOUR_START <= h < QUIET_HOUR_END
    return h >= QUIET_HOUR_START or h < QUIET_HOUR_END


# ── GitHub API ────────────────────────────────────────────────────────────────

def _gh_get(path: str) -> list | dict | None:
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept":        "application/vnd.github+json",
            "User-Agent":    "thefranceway-git-content-daemon/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.error(f"GitHub API error {path}: {e}")
        return None


def _fetch_new_prs(repo: str, last_merged_at: str) -> list[dict]:
    data = _gh_get(f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=20")
    if not data:
        return []
    new_prs = []
    for pr in data:
        merged_at = pr.get("merged_at")
        if not merged_at:
            continue
        if last_merged_at and merged_at <= last_merged_at:
            continue
        new_prs.append({
            "type":        "pr_merged",
            "title":       pr.get("title", ""),
            "description": (pr.get("body") or "")[:400],
            "url":         pr.get("html_url", ""),
            "merged_at":   merged_at,
        })
    return new_prs


def _fetch_new_tags(repo: str, last_tag: str) -> list[dict]:
    tags = _gh_get(f"/repos/{repo}/tags?per_page=10")
    if not tags:
        return []
    new_tags = []
    for tag in tags:
        name = tag.get("name", "")
        if name == last_tag:
            break
        # Fetch release notes if available
        rel = _gh_get(f"/repos/{repo}/releases/tags/{name}")
        body = ""
        if rel and isinstance(rel, dict):
            body = (rel.get("body") or "")[:400]
        new_tags.append({
            "type":        "tag_released",
            "title":       name,
            "description": body,
            "url":         f"https://github.com/{repo}/releases/tag/{name}",
        })
    return new_tags


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _tg_post(endpoint: str, payload: dict) -> dict | None:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{BOT_API}/{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.error(f"Telegram {endpoint} error: {e}")
        return None


def _send_approval_request(pending_id: str, tweet: str, repo: str, event_title: str) -> int | None:
    """Send a tweet draft to Telegram with approve/reject buttons. Returns message_id."""
    repo_short = repo.split("/")[-1]
    text = (
        f"<b>Git Content</b> — {repo_short}\n"
        f"<i>{event_title[:80]}</i>\n\n"
        f"Draft tweet:\n<code>{tweet}</code>\n\n"
        f"<i>{len(tweet)}/280 chars</i>"
    )
    result = _tg_post("sendMessage", {
        "chat_id":                  OWNER_CHAT_ID,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Post",  "callback_data": f"gc_approve:{pending_id}"},
                {"text": "✏️ Edit",  "callback_data": f"gc_edit:{pending_id}"},
                {"text": "❌ Skip",  "callback_data": f"gc_reject:{pending_id}"},
            ]]
        },
    })
    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def _answer_callback(callback_id: str, text: str) -> None:
    _tg_post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def _send_dm(text: str) -> None:
    _tg_post("sendMessage", {
        "chat_id":    OWNER_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    })


# ── Twitter OAuth 1.0a ────────────────────────────────────────────────────────

def _tw_auth_header(method: str, url: str, body_params: dict) -> str:
    oauth = {
        "oauth_consumer_key":     TW_API_KEY,
        "oauth_nonce":            uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        str(int(time.time())),
        "oauth_token":            TW_ACCESS_TOKEN,
        "oauth_version":          "1.0",
    }
    all_params = {**body_params, **oauth}
    enc = lambda s: urllib.parse.quote(str(s), safe="")
    param_str = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(all_params.items()))
    base = f"{method.upper()}&{enc(url)}&{enc(param_str)}"
    key  = f"{enc(TW_API_SECRET)}&{enc(TW_ACCESS_SECRET)}"
    sig  = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in sorted(oauth.items()))


def _post_tweet(text: str) -> bool:
    url     = "https://api.twitter.com/2/tweets"
    payload = json.dumps({"text": text}).encode()
    auth    = _tw_auth_header("POST", url, {})
    req     = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": auth,
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
            result = json.loads(resp.read())
        tweet_id = result.get("data", {}).get("id")
        if tweet_id:
            log.info(f"Tweet posted: {tweet_id}")
            return True
        log.error(f"Twitter unexpected response: {result}")
        return False
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"Twitter post failed {e.code}: {body}")
        return False
    except Exception as e:
        log.error(f"Twitter post error: {e}")
        return False


# ── Approval processing ───────────────────────────────────────────────────────

def _process_callbacks(state: dict, pending: list) -> tuple[dict, list]:
    """Check Telegram for callback query answers and process them."""
    offset   = state.get("tg_offset", 0)
    updates  = _tg_post("getUpdates", {"offset": offset, "timeout": 0, "limit": 20})

    if not updates or not updates.get("ok"):
        return state, pending

    for update in updates.get("result", []):
        state["tg_offset"] = update["update_id"] + 1

        cb = update.get("callback_query")
        if not cb:
            continue

        data        = cb.get("data", "")
        callback_id = cb["id"]

        if not data.startswith("gc_"):
            continue

        action, pending_id = data.split(":", 1)

        item = next((p for p in pending if p["id"] == pending_id), None)
        if not item:
            _answer_callback(callback_id, "Already handled.")
            continue

        if action == "gc_approve":
            today_count = _posts_today(state)
            if today_count >= MAX_POSTS_PER_DAY:
                _answer_callback(callback_id, "Daily limit reached — not posted.")
                log.info(f"Daily limit hit, skipping approved tweet: {item['tweet'][:60]}")
            else:
                ok = _post_tweet(item["tweet"])
                if ok:
                    state["posts_today"] = today_count + 1
                    _answer_callback(callback_id, "Posted.")
                    log.info(f"Tweet posted via approval: {item['tweet'][:60]}")
                else:
                    _answer_callback(callback_id, "Post failed — check logs.")
            pending = [p for p in pending if p["id"] != pending_id]

        elif action == "gc_reject":
            _answer_callback(callback_id, "Skipped.")
            log.info(f"Tweet rejected: {item['tweet'][:60]}")
            pending = [p for p in pending if p["id"] != pending_id]

        elif action == "gc_edit":
            _answer_callback(callback_id, "Reply to this message with your edited tweet to post it.")
            # Mark as waiting for reply — for now just keep it pending
            item["awaiting_edit"] = True

    return state, pending


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("Git Content Daemon — run start")

    state   = _load_state()
    pending = _load_pending()

    # 1. Process any pending Telegram callbacks first
    state, pending = _process_callbacks(state, pending)
    _save_pending(pending)
    _save_state(state)

    # 2. Check quiet hours before drafting new content
    if _in_quiet_hours():
        log.info("Quiet hours — skipping new event check")
        _save_state(state)
        return

    # 3. Check daily limit
    if _posts_today(state) >= MAX_POSTS_PER_DAY:
        log.info(f"Daily limit ({MAX_POSTS_PER_DAY}) reached — skipping new event check")
        _save_state(state)
        return

    # 4. Check how many items already pending approval (don't flood)
    if len(pending) >= 3:
        log.info("3 items already pending approval — skipping new event check")
        _save_state(state)
        return

    # 5. Poll repos for new events
    from agents.git_content_agent import draft_tweet

    new_items = 0
    for repo in WATCHED_REPOS:
        repo_state   = state["repos"].get(repo, {})
        last_merged  = repo_state.get("last_pr_merged_at", "")
        last_tag     = repo_state.get("last_tag", "")

        # Merged PRs
        new_prs = _fetch_new_prs(repo, last_merged)
        for pr in new_prs[:2]:   # max 2 per repo per run
            tweet = draft_tweet(
                repo=repo,
                event_type="pr_merged",
                title=pr["title"],
                description=pr.get("description", ""),
                url=pr.get("url", ""),
            )
            if tweet:
                pending_id = uuid.uuid4().hex[:12]
                msg_id     = _send_approval_request(pending_id, tweet, repo, pr["title"])
                if msg_id:
                    pending.append({
                        "id":          pending_id,
                        "tweet":       tweet,
                        "repo":        repo,
                        "event_type":  "pr_merged",
                        "event_title": pr["title"],
                        "message_id":  msg_id,
                        "created_at":  datetime.now(timezone.utc).isoformat(),
                    })
                    log.info(f"Draft sent for approval: [{repo}] {pr['title'][:60]}")
                    new_items += 1
            # Update last seen regardless (even if noise, don't re-check)
            if not last_merged or pr["merged_at"] > last_merged:
                state["repos"].setdefault(repo, {})["last_pr_merged_at"] = pr["merged_at"]

        # New tags
        new_tags = _fetch_new_tags(repo, last_tag)
        for tag in new_tags[:1]:   # max 1 tag per repo per run
            tweet = draft_tweet(
                repo=repo,
                event_type="tag_released",
                title=tag["title"],
                description=tag.get("description", ""),
                url=tag.get("url", ""),
            )
            if tweet:
                pending_id = uuid.uuid4().hex[:12]
                msg_id     = _send_approval_request(pending_id, tweet, repo, f"release {tag['title']}")
                if msg_id:
                    pending.append({
                        "id":          pending_id,
                        "tweet":       tweet,
                        "repo":        repo,
                        "event_type":  "tag_released",
                        "event_title": tag["title"],
                        "message_id":  msg_id,
                        "created_at":  datetime.now(timezone.utc).isoformat(),
                    })
                    log.info(f"Draft sent for approval: [{repo}] tag {tag['title']}")
                    new_items += 1
            if new_tags:
                state["repos"].setdefault(repo, {})["last_tag"] = new_tags[0]["title"]

    # 6. Daily digest at 9pm UTC
    now = datetime.now(timezone.utc)
    last_digest = state.get("last_digest_date", "")
    if now.hour == 21 and last_digest != now.strftime("%Y-%m-%d"):
        posted_today = state.get("posts_today", 0)
        pending_count = len(pending)
        _send_dm(
            f"<b>Git Content — daily summary</b>\n"
            f"Posted today: {posted_today}/{MAX_POSTS_PER_DAY}\n"
            f"Pending approval: {pending_count}\n"
            f"New drafts this run: {new_items}"
        )
        state["last_digest_date"] = now.strftime("%Y-%m-%d")

    _save_pending(pending)
    _save_state(state)
    log.info(f"Run complete — {new_items} new drafts, {len(pending)} pending")


if __name__ == "__main__":
    main()
