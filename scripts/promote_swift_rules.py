#!/usr/bin/env python3
"""
Swift Rule Promoter
====================
Reads the AD4M build knowledge graph for error→fix pairs that have been
applied 3+ times. Promotes qualifying patterns to MetaClaw skill files.

Run automatically every 10 pipeline executions (triggered by apple_build_pipeline.py).
Can also be run manually: python3 promote_swift_rules.py

Self-modification layer: patterns the stack discovers become permanent rules.
"""

import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.ad4m_tools import execute_ad4m_tool

PERSPECTIVE_UUID  = "a47bf0c3-5a86-4367-a462-f88680491525"
METACLAW_SKILLS   = Path.home() / ".metaclaw" / "skills"
PROMOTION_LOG     = Path.home() / ".metaclaw" / "records" / "promotions.jsonl"
MIN_APPLY_COUNT   = 3


def _get_all_fix_pairs() -> list[dict]:
    """Query AD4M for all error→fix edges."""
    try:
        result = execute_ad4m_tool("ad4m_read_links", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    None,
            "predicate": "franc://fixed-by",
        })
        data = json.loads(result)
        return data.get("links", [])
    except Exception as e:
        print(f"[Promoter] AD4M query failed: {e}")
        return []


def _get_fix_content(fix_uri: str) -> str:
    """Get the content of a fix node."""
    try:
        result = execute_ad4m_tool("ad4m_read_links", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    fix_uri,
            "predicate": "franc://has-content",
        })
        data = json.loads(result)
        links = data.get("links", [])
        if links:
            target = links[0].get("data", {}).get("target", "")
            return target.replace("literal://", "")
    except Exception:
        pass
    return ""


def _already_promoted(fix_uri: str) -> bool:
    """Check if this fix has already been promoted to a MetaClaw skill."""
    try:
        result = execute_ad4m_tool("ad4m_read_links", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    fix_uri,
            "predicate": "franc://promoted-to",
        })
        data = json.loads(result)
        return len(data.get("links", [])) > 0
    except Exception:
        return False


def _count_applications(error_uri: str) -> int:
    """Count how many times this error pattern has been fixed."""
    try:
        result = execute_ad4m_tool("ad4m_read_links", {
            "perspective_uuid": PERSPECTIVE_UUID,
            "source":    error_uri,
            "predicate": "franc://fixed-by",
        })
        data = json.loads(result)
        return len(data.get("links", []))
    except Exception:
        return 0


def _write_metaclaw_skill(fix_uri: str, fix_content: str, error_hash: str) -> Path:
    """Generate a MetaClaw skill file for a promoted fix pattern."""
    METACLAW_SKILLS.mkdir(parents=True, exist_ok=True)

    skill_name = f"swift-fix-{error_hash[:8]}"
    skill_path = METACLAW_SKILLS / f"{skill_name}.md"

    # Parse fix content
    summary  = fix_content if len(fix_content) < 200 else fix_content[:200]
    promoted = datetime.now().strftime("%Y-%m-%d")

    content = f"""---
name: {skill_name}
type: swift_build_fix
promoted: {promoted}
apply_count: {MIN_APPLY_COUNT}+
---

## Swift Build Fix — Auto-Promoted Pattern

This pattern was applied {MIN_APPLY_COUNT}+ times during iOS builds and promoted automatically.

## Error Pattern

Error hash: `{error_hash}`

## Canonical Fix

{summary}

## When to Apply

Apply this fix when the build agent encounters this error pattern.
The Error Fix Agent checks AD4M before generating new fixes — this skill
provides the canonical solution so the agent can apply it immediately.

## Why It Was Promoted

Repeated occurrence across multiple build cycles indicates a systematic
pattern worth encoding as a permanent rule rather than re-deriving each time.
"""

    skill_path.write_text(content, encoding="utf-8")
    return skill_path


def _record_promotion(fix_uri: str, skill_path: Path) -> None:
    """Write promotion event to AD4M and the local promotion log."""
    execute_ad4m_tool("ad4m_write_link", {
        "perspective_uuid": PERSPECTIVE_UUID,
        "source":    fix_uri,
        "predicate": "franc://promoted-to",
        "target":    f"literal://{skill_path}",
    })

    PROMOTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "timestamp":  datetime.now().isoformat(),
        "fix_uri":    fix_uri,
        "skill_path": str(skill_path),
    })
    with open(PROMOTION_LOG, "a") as f:
        f.write(entry + "\n")


def promote() -> dict:
    print("[Promoter] Scanning AD4M for promotable fix patterns...")

    fix_pairs  = _get_all_fix_pairs()
    promoted   = []
    skipped    = []

    # Group by error_uri to count applications
    error_counts: dict[str, list[str]] = {}
    for link in fix_pairs:
        data      = link.get("data", {})
        error_uri = data.get("source", "")
        fix_uri   = data.get("target", "")
        if error_uri.startswith("build://error/") and fix_uri.startswith("build://fix/"):
            if error_uri not in error_counts:
                error_counts[error_uri] = []
            error_counts[error_uri].append(fix_uri)

    print(f"[Promoter] Found {len(error_counts)} unique error patterns\n")

    for error_uri, fix_uris in error_counts.items():
        count      = len(fix_uris)
        error_hash = error_uri.replace("build://error/", "")

        if count < MIN_APPLY_COUNT:
            skipped.append({"error_uri": error_uri, "count": count, "reason": f"below threshold ({count} < {MIN_APPLY_COUNT})"})
            continue

        for fix_uri in fix_uris[:1]:  # promote the first (earliest) fix
            if _already_promoted(fix_uri):
                skipped.append({"fix_uri": fix_uri, "reason": "already promoted"})
                continue

            fix_content = _get_fix_content(fix_uri)
            if not fix_content:
                skipped.append({"fix_uri": fix_uri, "reason": "no content in AD4M"})
                continue

            skill_path = _write_metaclaw_skill(fix_uri, fix_content, error_hash)
            _record_promotion(fix_uri, skill_path)

            print(f"[Promoter] PROMOTED: {error_hash[:8]} → {skill_path.name}")
            promoted.append({
                "error_hash": error_hash,
                "fix_uri":    fix_uri,
                "skill":      str(skill_path),
                "count":      count,
            })

    result = {
        "promoted_count": len(promoted),
        "skipped_count":  len(skipped),
        "promoted":       promoted,
        "timestamp":      datetime.now().isoformat(),
    }

    print(f"\n[Promoter] Done — {len(promoted)} patterns promoted, {len(skipped)} skipped")
    return result


if __name__ == "__main__":
    result = promote()
    print(json.dumps(result, indent=2))
