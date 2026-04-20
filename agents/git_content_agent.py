#!/usr/bin/env python3
"""
Git Content Agent — drafts tweets from git events in Francesca's voice.

Called by git_content_daemon.py when a merged PR or new tag is detected.
Returns a tweet draft (max 280 chars). Does not post — daemon handles that.

Usage:
    python git_content_agent.py --repo franc-token --type pr_merged \
        --title "Add FRANC gate to /task endpoint" --url "https://github.com/..."
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

PLATFORM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLATFORM_DIR))

# ── Voice guidelines ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are writing tweets for Francesca Ranieri (@thefranceway).

Her voice: behavioral researcher at the intersection of longevity, decentralized tech,
and behavioral psychology. Precise, non-obvious, depth-seeking. Never hype.
Lead with insight, not summary. Write like a person, not a product announcement.

Rules — follow all of them:
- Max 260 characters (leave room for a link if needed)
- No exclamation marks. Ever.
- No em dashes (— or –). Use a period or comma instead.
- No corporate words: excited, thrilled, proud, announce, leverage, synergy
- No "we just shipped" or "introducing" framing
- Short sentences. Fragments are fine.
- Lead with what changed or what it means, not what it is
- Lowercase is fine for emphasis on key terms
- No hashtags unless they appear naturally
- One tweet only. No thread unless asked.

Good: "the FRANC gate is live. agents without a wallet don't pass."
Bad: "Excited to announce we've shipped our new token-gating feature!"

Return only the tweet text. Nothing else. No quotes around it."""


NOISE_TITLES = {
    "fix", "wip", "typo", "bump", "chore", "merge", "revert",
    "update deps", "update dependencies", "minor", "cleanup", "lint",
    "formatting", "whitespace", "readme", "changelog",
}


def is_noise(title: str) -> bool:
    t = title.lower().strip()
    if len(t) < 8:
        return True
    for noise in NOISE_TITLES:
        if t == noise or t.startswith(noise + " ") or t.startswith(noise + ":"):
            return True
    return False


def draft_tweet(
    repo: str,
    event_type: str,
    title: str,
    description: str = "",
    url: str = "",
) -> str | None:
    """
    Draft a tweet for a git event.
    Returns tweet text or None if the event is too noisy to tweet.
    """
    if is_noise(title):
        return None

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    repo_short = repo.split("/")[-1]  # "thefranceway/franc-token" → "franc-token"

    if event_type == "pr_merged":
        user_msg = (
            f"A pull request was just merged in the {repo_short} repo.\n\n"
            f"PR title: {title}\n"
        )
        if description:
            user_msg += f"Description: {description[:300]}\n"
        if url:
            user_msg += f"URL: {url}\n"
        user_msg += "\nWrite a tweet about this."

    elif event_type == "tag_released":
        user_msg = (
            f"A new release was tagged in the {repo_short} repo.\n\n"
            f"Tag/version: {title}\n"
        )
        if description:
            user_msg += f"Release notes: {description[:300]}\n"
        if url:
            user_msg += f"URL: {url}\n"
        user_msg += "\nWrite a tweet about this release."

    else:
        return None

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=120,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    # Log token usage to shared token_usage.jsonl
    try:
        usage = response.usage
        record = {
            "ts":    datetime.now(timezone.utc).isoformat(),
            "agent": "git_content_agent",
            "model": "claude-haiku-4-5-20251001",
            "in":    usage.input_tokens,
            "out":   usage.output_tokens,
        }
        log_path = PLATFORM_DIR / "logs" / "token_usage.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    tweet = response.content[0].text.strip().strip('"').strip("'")

    # Hard truncate at 280 just in case
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    return tweet


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Draft a tweet from a git event")
    parser.add_argument("--repo",        required=True, help="e.g. thefranceway/franc-token")
    parser.add_argument("--type",        required=True, choices=["pr_merged", "tag_released"])
    parser.add_argument("--title",       required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--url",         default="")
    args = parser.parse_args()

    result = draft_tweet(
        repo=args.repo,
        event_type=args.type,
        title=args.title,
        description=args.description,
        url=args.url,
    )

    if result is None:
        print("[SKIPPED] Noise event — no tweet drafted.")
    else:
        print(result)
