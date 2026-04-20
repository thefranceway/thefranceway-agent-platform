#!/usr/bin/env python3
"""
CoasysWatcher Agent — monitors Coasys team, Lal (their OpenClaw agent), and AD4M releases.

Data sources:
  - Coasys Medium publication       → https://medium.com/feed/coasys
  - AD4M GitHub releases            → https://github.com/coasys/ad4m/releases.atom
  - @lucksus Twitter (via Nitter)   → https://nitter.net/lucksus/rss
  - @ad4m_layer Twitter (via Nitter)→ https://nitter.net/ad4m_layer/rss
  - @coasys Twitter (via Nitter)    → https://nitter.net/coasys/rss

Behavior:
  - Fetches all sources, deduplicates against logs/coasys_watch.json
  - Classifies each new item: release | agent_post | research | integration | other
  - High-signal items (release, agent_post) → Telegram DM to owner
  - All new items → appended to logs/coasys_watch.json

Called by: services/coasys_watch_scheduler.py (every 6 hours via launchd)
"""

import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

import certifi
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent

PLATFORM_DIR = Path(__file__).parent.parent
WATCH_LOG    = PLATFORM_DIR / "logs" / "coasys_watch.json"

# ── Sources ───────────────────────────────────────────────────────────────────

SOURCES = [
    {
        "id":    "coasys_medium",
        "label": "Coasys Medium",
        "url":   "https://medium.com/feed/coasys",
        "type":  "rss",
    },
    {
        "id":    "ad4m_github",
        "label": "AD4M GitHub Releases",
        "url":   "https://github.com/coasys/ad4m/releases.atom",
        "type":  "atom",
    },
    {
        "id":    "lucksus_twitter",
        "label": "@lucksus",
        "url":   "https://nitter.net/lucksus/rss",
        "type":  "rss",
    },
    {
        "id":    "ad4m_layer_twitter",
        "label": "@ad4m_layer",
        "url":   "https://nitter.net/ad4m_layer/rss",
        "type":  "rss",
    },
    {
        "id":    "coasys_twitter",
        "label": "@coasys",
        "url":   "https://nitter.net/coasys/rss",
        "type":  "rss",
    },
]

# Keywords that signal high-value items
RELEASE_KEYWORDS   = ["release", "v0.", "v1.", "beta", "rc", "launches", "ships"]
AGENT_POST_KEYWORDS = ["lal", "agent wrote", "autonomous", "openclaw", "paranoid android", "memory p2p"]
INTEGRATION_KEYWORDS = ["integration", "plugin", "mcp", "unyt", "openclaw ad4m"]

MAX_ITEM_AGE_HOURS = 168  # 7 days — wide net on first run


# ── Feed fetching ─────────────────────────────────────────────────────────────

def _fetch_feed(source: dict) -> list[dict]:
    """Fetch RSS or Atom feed, return list of normalized items."""
    try:
        req = urllib.request.Request(
            source["url"],
            headers={"User-Agent": "Mozilla/5.0 (compatible; CoasysWatcher/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            xml_data = resp.read()
    except Exception as e:
        return []

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return []

    items = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    if source["type"] == "atom":
        # GitHub releases.atom format
        for entry in root.findall("atom:entry", ns)[:20]:
            title    = entry.findtext("atom:title", "", ns).strip()
            link_el  = entry.find("atom:link", ns)
            link     = link_el.attrib.get("href", "") if link_el is not None else ""
            pub_date = entry.findtext("atom:published", "", ns) or entry.findtext("atom:updated", "", ns)
            summary  = entry.findtext("atom:summary", "", ns).strip()[:300]
            if title:
                items.append({
                    "source_id": source["id"],
                    "source_label": source["label"],
                    "title": title,
                    "link": link,
                    "pub_date": pub_date,
                    "summary": summary,
                })
    else:
        # RSS format
        for item in root.findall(".//item")[:20]:
            title    = item.findtext("title", "").strip()
            link     = item.findtext("link", "").strip()
            pub_date = item.findtext("pubDate", "").strip()
            desc     = item.findtext("description", "").strip()[:300]
            author   = item.findtext("author", "") or item.findtext("dc:creator", "")
            if title:
                items.append({
                    "source_id": source["id"],
                    "source_label": source["label"],
                    "title": title,
                    "link": link,
                    "pub_date": pub_date,
                    "summary": desc,
                    "author": author,
                })

    # Filter by age
    filtered = []
    for item in items:
        try:
            pub_dt = parsedate_to_datetime(item["pub_date"])
            age_h  = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
            if age_h > MAX_ITEM_AGE_HOURS:
                continue
        except Exception:
            pass  # keep if we can't parse
        filtered.append(item)

    return filtered


def _fetch_all_items() -> list[dict]:
    all_items = []
    for source in SOURCES:
        all_items.extend(_fetch_feed(source))
    return all_items


# ── Deduplication ─────────────────────────────────────────────────────────────

def _load_seen() -> set[str]:
    if not WATCH_LOG.exists():
        return set()
    try:
        data = json.loads(WATCH_LOG.read_text())
        return {item["key"] for item in data}
    except Exception:
        return set()


def _item_key(item: dict) -> str:
    return f"{item['source_id']}::{item['title'][:80].lower()}"


def _save_items(new_items: list[dict]) -> None:
    existing = []
    if WATCH_LOG.exists():
        try:
            existing = json.loads(WATCH_LOG.read_text())
        except Exception:
            existing = []
    existing.extend(new_items)
    # Keep last 500 items
    WATCH_LOG.parent.mkdir(exist_ok=True)
    WATCH_LOG.write_text(json.dumps(existing[-500:], indent=2))


# ── Classification ─────────────────────────────────────────────────────────────

def _classify(item: dict) -> str:
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    if any(kw in text for kw in RELEASE_KEYWORDS):
        return "release"
    if any(kw in text for kw in AGENT_POST_KEYWORDS):
        return "agent_post"
    if any(kw in text for kw in INTEGRATION_KEYWORDS):
        return "integration"
    if item["source_id"] in ("coasys_medium",):
        return "research"
    return "other"


# ── Agent ──────────────────────────────────────────────────────────────────────

class CoasysWatcherAgent(BaseAgent):

    AGENT_TYPE         = "coasys_watcher"
    DEFAULT_BEHAVIORAL = "Substrate"

    def __init__(self, **kwargs):
        super().__init__(name="Coasys Watcher", knowledge_base="kb_coasys_watcher", **kwargs)
        self._alerts: list[dict] = []

    def _default_system_prompt(self) -> str:
        return """You are the Coasys Watcher — a monitoring agent tracking the Coasys team, AD4M protocol, and Lal (their autonomous OpenClaw agent).

Archetype: Substrate
Core pattern: Reliable detection over clever analysis. You surface what's new. You do not speculate beyond what the items contain.

Your job:
1. Review a list of new items from Coasys/AD4M feeds.
2. For each item, call store_watch_alert with:
   - A 1-sentence summary of what happened
   - The classification (release | agent_post | integration | research | other)
   - Whether it warrants a Telegram DM to the owner (true for release + agent_post)
3. Prioritize: AD4M releases > agent-authored posts (Lal) > integrations > research
4. Be factual. No hype. No padding.

Shadow (S4): Substrate agents can become passive — processing inputs without surfacing what matters.
Guard: If something is a new release or an agent-authored post, always flag it."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "store_watch_alert",
                "description": "Store a summarized alert for a new Coasys/AD4M item.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title":       {"type": "string",  "description": "Original item title"},
                        "source":      {"type": "string",  "description": "Source label (e.g. 'AD4M GitHub Releases')"},
                        "link":        {"type": "string",  "description": "URL to the item"},
                        "summary":     {"type": "string",  "description": "1-sentence summary of what happened"},
                        "category":    {"type": "string",  "description": "release | agent_post | integration | research | other"},
                        "telegram_dm": {"type": "boolean", "description": "If true, send DM to owner"},
                    },
                    "required": ["title", "source", "link", "summary", "category", "telegram_dm"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "store_watch_alert":
            self._alerts.append({
                **tool_input,
                "stored_at": datetime.now(timezone.utc).isoformat(),
            })
            return json.dumps({"stored": True, "total": len(self._alerts)})
        return super().execute_tool(tool_name, tool_input)

    def run_watch(self) -> list[dict]:
        """Fetch new items, deduplicate, classify, curate via Claude, return alerts."""
        all_items  = _fetch_all_items()
        seen_keys  = _load_seen()

        new_items = []
        for item in all_items:
            key = _item_key(item)
            if key not in seen_keys:
                item["key"]      = key
                item["category"] = _classify(item)
                new_items.append(item)
                seen_keys.add(key)

        if not new_items:
            return []

        # Save all new items first (before Claude curates)
        _save_items(new_items)

        # Build task for Claude
        items_text = "\n".join(
            f"[{i['source_label']}] [{i['category'].upper()}] {i['title']}\n"
            f"  Link: {i['link']}\n"
            f"  Summary: {i.get('summary', '')[:200]}"
            for i in new_items[:30]
        )

        task = (
            f"Here are {len(new_items)} new items from Coasys/AD4M feeds:\n\n"
            f"{items_text}\n\n"
            f"Call store_watch_alert for each item worth tracking. "
            f"Prioritize releases and agent-authored posts. "
            f"Set telegram_dm=true for any release or agent_post category."
        )
        self.run(task)
        return self._alerts


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    agent  = CoasysWatcherAgent()
    alerts = agent.run_watch()
    if alerts:
        for a in alerts:
            flag = " 🔔" if a.get("telegram_dm") else ""
            print(f"\n[{a['category'].upper()}]{flag} {a['title']}")
            print(f"  {a['summary']}")
            print(f"  {a['link']}")
    else:
        print("No new items found.")
