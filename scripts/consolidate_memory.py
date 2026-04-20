#!/usr/bin/env python3
"""
Memory Consolidation — runs on a schedule via launchd.
Groups recent agent runs into episodes and writes summaries to kb_shared.
Run every 6 hours so kb_shared stays current.
"""

import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

PLATFORM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLATFORM_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [consolidate] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

from core.episodic_memory import EpisodicMemory, JSONVectorStore

SHARED_KB = "kb_shared"


def main():
    log.info("Starting memory consolidation")

    em = EpisodicMemory()

    episodes = em.build_episodes()
    log.info(f"Found {len(episodes)} total episodes")

    written = em.consolidate()
    log.info(f"Wrote {written} new episodes to {SHARED_KB}")

    # Report current kb_shared state
    kb = JSONVectorStore(SHARED_KB)
    log.info(f"kb_shared now has {kb.count()} docs")

    # Log episode summary
    for ep in episodes[-3:]:
        log.info(f"  {ep['episode_id']} | {ep['start_time'][:16]} | {ep['run_count']} runs | agents: {', '.join(ep['agents'][:3])}")

    log.info("Consolidation complete")


if __name__ == "__main__":
    main()
