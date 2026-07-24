#!/usr/bin/env python3
"""
Crystallize Error Patterns — Weekly Cron
==========================================
Scans recent agent runs for unclassified errors, groups them by similarity,
proposes new regex patterns via Claude, and stores them in AD4M.

Usage:
    python scripts/crystallize_patterns.py            # scan last 500 violations
    python scripts/crystallize_patterns.py --dry-run  # show proposals, don't store

Weekly cron:
    0 9 * * 1  cd /path/to/agent-platform && python scripts/crystallize_patterns.py
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import anthropic

PLATFORM_DIR     = Path(__file__).parent.parent
VIOLATIONS_LOG   = PLATFORM_DIR / "logs" / "contract_violations.jsonl"
PERSPECTIVE_UUID = os.getenv("AD4M_ERROR_PERSPECTIVE", "a47bf0c3-5a86-4367-a462-f88680491525")
MAX_RECORDS      = 500
MIN_GROUP_SIZE   = 3   # minimum errors to propose a pattern for


# ── AD4M helper ───────────────────────────────────────────────────────────────

def _ad4m_write(source: str, predicate: str, target: str):
    try:
        sys.path.insert(0, str(PLATFORM_DIR))
        from agents.ad4m_tools import execute_ad4m_tool
        execute_ad4m_tool("ad4m_write_link", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    source,
            "predicate": predicate,
            "target":    target,
        })
    except Exception as e:
        print(f"  [AD4M write failed: {e}]")


# ── Pattern proposer ──────────────────────────────────────────────────────────

def propose_pattern(client: anthropic.Anthropic, messages: list[str], category: str) -> str:
    """Ask Claude to propose a regex pattern that matches these error messages."""
    sample = "\n".join(f"- {m}" for m in messages[:10])
    response = client.messages.create(
        model      = "claude-haiku-4-5-20251001",
        max_tokens = 150,
        system     = (
            "You are a regex expert. Given a list of similar error messages, "
            "propose a single Python regex pattern that matches all of them. "
            "Respond with ONLY the raw regex pattern string — no quotes, no explanation."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"These errors all belong to category '{category}'.\n"
                f"Propose a regex pattern that matches them:\n\n{sample}"
            ),
        }],
    )
    raw = response.content[0].text.strip()
    # Validate the regex
    re.compile(raw)
    return raw


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print proposals without storing to AD4M")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set — cannot propose patterns")
        sys.exit(1)

    if not VIOLATIONS_LOG.exists():
        print("No contract_violations.jsonl found — nothing to crystallize")
        return

    # Load last MAX_RECORDS violations
    records = []
    with open(VIOLATIONS_LOG) as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except Exception:
                continue
    records = records[-MAX_RECORDS:]

    # Find unclassified: violations where category/field indicates an unknown error type
    unclassified = [
        r for r in records
        if r.get("field") in ("root", "") or r.get("severity") == "hard"
    ]

    if not unclassified:
        print(f"No unclassified violations in last {len(records)} records — nothing to crystallize")
        return

    # Group by agent + field pattern
    groups: dict[str, list[str]] = defaultdict(list)
    for r in unclassified:
        key = f"{r.get('agent', 'unknown')}.{r.get('expected', 'unknown')}"
        groups[key].append(r.get("got", "")[:200])

    print(f"Found {len(unclassified)} unclassified violations across {len(groups)} groups")

    client = anthropic.Anthropic(api_key=api_key)
    stored = 0

    for group_key, messages in groups.items():
        if len(messages) < MIN_GROUP_SIZE:
            continue

        # Infer category from group key
        parts    = group_key.split(".")
        category = parts[-1] if len(parts) > 1 else "compile_error"

        print(f"\nGroup: {group_key} ({len(messages)} errors)")
        try:
            pattern = propose_pattern(client, messages, category)
            print(f"  Proposed pattern: {pattern!r}")

            if args.dry_run:
                print("  [dry-run] Not stored")
                continue

            _ad4m_write(
                source    = f"build://error-patterns/{category}",
                predicate = "franc://regex-pattern",
                target    = f"literal://{pattern}",
            )
            print(f"  Stored in AD4M at build://error-patterns/{category}")
            stored += 1

        except re.error as e:
            print(f"  Invalid regex proposed ({e}) — skipping")
        except Exception as e:
            print(f"  Error: {e}")

    if args.dry_run:
        print(f"\n[dry-run] Would have stored {sum(1 for g in groups.values() if len(g) >= MIN_GROUP_SIZE)} patterns")
    else:
        print(f"\nCrystallization complete — {stored} new patterns stored in AD4M")

    # Write session note
    try:
        sys.path.insert(0, str(PLATFORM_DIR))
        from agents.ad4m_tools import execute_ad4m_tool
        note = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "violations_scanned": len(records),
            "groups_found": len(groups),
            "patterns_stored": stored,
        })
        execute_ad4m_tool("ad4m_write_link", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    "build://crystallize/run",
            "predicate": "franc://session-note",
            "target":    f"literal://{note}",
        })
    except Exception:
        pass


if __name__ == "__main__":
    main()
