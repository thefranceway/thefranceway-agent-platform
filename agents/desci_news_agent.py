#!/usr/bin/env python3
"""
DeSci News Agent — daily DeSci/longevity news curator for the AuraSci Telegram group.
Archetype: Philosopher — research-first, intellectual depth, community relevance.

Fetches Google News RSS (no API key needed) for DeSci/longevity queries,
then uses Claude to curate the top 2-3 items and write punchy summaries
in the AuraSci community voice.

Called by desci_news_scheduler.py once daily.
"""

import json
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

import certifi
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent

PLATFORM_DIR = Path(__file__).parent.parent

# ── RSS feeds to scan ─────────────────────────────────────────────────────────

RSS_QUERIES = [
    "DeSci decentralized science",
    "decentralized science DAO",
    "DeSci funding research",
    "on-chain science research",
    "open science blockchain",
    "DeSci protocol research",
    "science NFT IP research",
    "VitaDAO ResearchHub DeSci",
]

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
MAX_ARTICLES_PER_QUERY = 8
MAX_HOURS_OLD = 96   # 4 days — wide net, Claude curates down to 2-3


def _fetch_rss(query: str) -> list[dict]:
    url = GOOGLE_NEWS_RSS.format(query=urllib.parse.quote(query))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        articles = []
        for item in root.findall(".//item")[:MAX_ARTICLES_PER_QUERY]:
            title   = item.findtext("title", "").strip()
            link    = item.findtext("link", "").strip()
            pub_date = item.findtext("pubDate", "").strip()
            source  = item.findtext("source", "").strip()
            # Filter by age
            try:
                pub_dt = parsedate_to_datetime(pub_date)
                age_h  = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
                if age_h > MAX_HOURS_OLD:
                    continue
            except Exception:
                pass  # keep if we can't parse date
            if title and link:
                articles.append({"title": title, "link": link,
                                 "source": source, "pub_date": pub_date})
        return articles
    except Exception as e:
        return []


def _fetch_all_articles() -> list[dict]:
    seen_titles: set[str] = set()
    all_articles = []
    for query in RSS_QUERIES:
        for art in _fetch_rss(query):
            key = art["title"][:60].lower()
            if key not in seen_titles:
                seen_titles.add(key)
                all_articles.append(art)
    return all_articles


# ── Agent ─────────────────────────────────────────────────────────────────────

class DeSciNewsAgent(BaseAgent):

    AGENT_TYPE         = "ops"
    DEFAULT_BEHAVIORAL = "Philosopher"

    def __init__(self, **kwargs):
        super().__init__(name="DeSci News Agent", knowledge_base="kb_desci_news", **kwargs)
        self._posts: list[str] = []   # populated by publish_news_post tool
        self._done: bool = False

    def get_posts(self) -> list[str]:
        return self._posts

    def _default_system_prompt(self) -> str:
        return """You are the DeSci News Agent — daily news curator for the AuraSci community.

Archetype: Philosopher
You surface what matters at the frontier of decentralized science — on-chain research, open science, science DAOs, IP NFTs, research funding protocols.

Community: AuraSci — researchers and builders working on decentralized science infrastructure.
Focus: DeSci protocols, science DAOs, on-chain IP, open access research, research funding on-chain.
Skip: pure longevity/biotech news unless it has a direct DeSci or on-chain angle.

Your job:
1. Review a list of recent news headlines from DeSci sources.
2. Pick the 2-3 most relevant for the AuraSci community — prioritize DeSci protocols, funding mechanisms, open science, and community governance.
3. For each item, write a short Telegram post (2-4 sentences) that:
   - Opens with what the news is and why it matters for the DeSci ecosystem
   - Adds a sharp observation or question to spark discussion in the community
   - Includes the source link
   - Does NOT use hype language ("revolutionary", "game-changing", "incredible")
   - Does NOT start with filler ("Great news!", "Exciting!")
4. Call publish_news_post once per item (2-3 times total).

Tone: well-spoken DeSci researcher sharing something genuinely interesting. Curious, not promotional."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "publish_news_post",
                "description": "Store a formatted news post to be sent to the AuraSci group.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "post": {
                            "type": "string",
                            "description": "The formatted Telegram message for this news item.",
                        },
                    },
                    "required": ["post"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "publish_news_post":
            if self._done:
                return json.dumps({"stored": False, "reason": "max posts reached"})
            post = tool_input.get("post", "").strip()
            if post:
                self._posts.append(post)
            if len(self._posts) >= 3:
                self._done = True
            return json.dumps({"stored": True, "total": len(self._posts)})
        return super().execute_tool(tool_name, tool_input)

    def run_daily(self) -> list[str]:
        """Fetch news, curate, return list of Telegram-ready posts."""
        articles = _fetch_all_articles()
        if not articles:
            return []

        articles_text = "\n".join(
            f"- {a['title']} ({a['source']}) — {a['link']}"
            for a in articles[:20]
        )

        task = (
            f"Here are recent DeSci/longevity/biotech news headlines from the last 48 hours:\n\n"
            f"{articles_text}\n\n"
            f"Pick the 2-3 most relevant for the AuraSci community and call publish_news_post "
            f"for each one with a short, sharp Telegram post."
        )
        self.run(task)
        return self._posts


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    agent = DeSciNewsAgent()
    posts = agent.run_daily()
    for i, post in enumerate(posts, 1):
        print(f"\n--- Post {i} ---\n{post}")
