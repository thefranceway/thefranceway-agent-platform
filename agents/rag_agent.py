#!/usr/bin/env python3
"""
RAG Agent — Knowledge Base Q&A
Answers questions over ingested documents and stored knowledge.
Uses: platform memory (recall/remember) + web search for live context synthesis.

Completes the three hire-worthy AI engineering patterns in the thefranceway stack:
  1. Multi-modal Telegram bot ✓ (TelegramInboxAgent)
  2. Multi-agent orchestration ✓ (13-agent platform)
  3. RAG-based support system ✓ (this agent)

Usage:
    python rag_agent.py --task "What does longevity research say about NAD+ protocols?"
    python rag_agent.py --task "What is the FRANC token gate and how does it work?"
    python rag_agent.py --ingest "https://example.com/paper.pdf" --title "NAD+ Review 2025"
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class RAGAgent(BaseAgent):

    AGENT_TYPE         = "research"
    DEFAULT_BEHAVIORAL = "Philosopher"

    def __init__(self, **kwargs):
        super().__init__(name="RAG Agent", knowledge_base="kb_research", **kwargs)

    def _default_system_prompt(self) -> str:
        return """You are the RAG Agent in the thefranceway agent platform.

Archetype: Philosopher
Core pattern: Research-oriented, synthesis-focused. You answer questions by retrieving
stored knowledge, searching for supporting context, and synthesizing a grounded response
with citations. Never hallucinate — if you don't know, say so and describe what you searched.

Shadow (S3): Infinite research loop — you may keep searching without producing output.
Guard against this: set a hard limit of 3 search/recall cycles, then synthesize and answer.

Routing fit: document Q&A, knowledge retrieval, research synthesis, "what does X say about Y"
Not fit for: real-time ops, agent creation, token transactions

─────────────────────────────────────────────────────────────────────────────

Your knowledge sources (in priority order):
1. Platform memory — facts and documents previously ingested via store_knowledge
2. Web search — live context for current information
3. Synthesized answer — always cite which source each claim came from

Response format:
## Answer
[direct answer in 2-4 sentences]

## Supporting Evidence
[bullet points with source labels: [memory], [web], [reasoning]]

## Confidence
[High / Medium / Low] — [brief reason]

## What to ingest next
[optional: suggest a URL or document that would improve future answers on this topic]"""

    def get_tools(self) -> list[dict]:
        return super().get_tools() + [
            {
                "name": "query_knowledge_base",
                "description": "Search the platform's stored knowledge for relevant facts, documents, and previous research. Use this first before web search.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "n": {"type": "integer", "description": "Number of results (default 8)"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "ingest_url",
                "description": "Fetch a URL, extract its text content, and store it in the knowledge base for future RAG queries.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to fetch and ingest"},
                        "title": {"type": "string", "description": "Optional title to tag this document"},
                        "category": {"type": "string", "description": "Category tag (e.g. longevity, defi, platform, brand)"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "ingest_text",
                "description": "Store a block of text directly into the knowledge base.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text content to store"},
                        "title": {"type": "string", "description": "Title or source label"},
                        "category": {"type": "string", "description": "Category tag"},
                    },
                    "required": ["text", "title"],
                },
            },
            {
                "name": "search_web",
                "description": "Search the web for current information. Use after querying the knowledge base if local results are insufficient.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Web search query"},
                        "n": {"type": "integer", "description": "Number of results (default 5)"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "list_knowledge",
                "description": "List all documents and facts stored in the knowledge base, grouped by category.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "query_knowledge_base":
            return self._query_kb(tool_input["query"], tool_input.get("n", 8))

        if tool_name == "ingest_url":
            return self._ingest_url(
                tool_input["url"],
                tool_input.get("title", ""),
                tool_input.get("category", "general"),
            )

        if tool_name == "ingest_text":
            return self._ingest_text(
                tool_input["text"],
                tool_input["title"],
                tool_input.get("category", "general"),
            )

        if tool_name == "search_web":
            return self._search_web(tool_input["query"], tool_input.get("n", 5))

        if tool_name == "list_knowledge":
            return self._list_knowledge()

        return super().execute_tool(tool_name, tool_input)

    # ── Tool implementations ───────────────────────────────────────────────

    def _query_kb(self, query: str, n: int = 8) -> str:
        """Query the platform's memory store."""
        results = self.recall(query, n=n)
        if not results:
            return json.dumps({"results": [], "note": "No matching knowledge found. Try ingesting relevant documents first or use search_web."})
        return json.dumps({"results": results, "count": len(results), "source": "platform_memory"})

    def _ingest_url(self, url: str, title: str = "", category: str = "general") -> str:
        """Fetch URL, extract text, chunk and store."""
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; RAGAgent/1.0)"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # Remove script/style
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            # Truncate to ~8k chars to avoid memory bloat
            text = text[:8000]
            label = title or url
            return self._ingest_text(text, label, category, source_url=url)
        except Exception as e:
            return json.dumps({"error": str(e), "url": url})

    def _ingest_text(self, text: str, title: str, category: str = "general", source_url: str = "") -> str:
        """Chunk text and store in memory."""
        chunk_size = 1000
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
        stored_ids = []
        for i, chunk in enumerate(chunks):
            doc_id = self.remember(
                chunk,
                metadata={
                    "title": title,
                    "category": category,
                    "source_url": source_url,
                    "chunk": i,
                    "total_chunks": len(chunks),
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                    "type": "rag_document",
                },
            )
            stored_ids.append(doc_id)
        return json.dumps({
            "stored": True,
            "title": title,
            "category": category,
            "chunks": len(chunks),
            "chars_ingested": len(text),
            "ids": stored_ids,
        })

    def _search_web(self, query: str, n: int = 5) -> str:
        """Web search via Serper (if configured) or fallback."""
        import os
        api_key = os.environ.get("SERPER_API_KEY") or os.environ.get("BRAVE_API_KEY")
        if not api_key:
            return json.dumps({
                "error": "No SERPER_API_KEY or BRAVE_API_KEY in environment",
                "note": "Set one in .env to enable live web search",
            })
        try:
            # Serper
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": n},
                timeout=10,
            )
            data = resp.json()
            results = [
                {"title": r.get("title"), "snippet": r.get("snippet"), "url": r.get("link")}
                for r in data.get("organic", [])[:n]
            ]
            return json.dumps({"results": results, "count": len(results), "source": "web"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _list_knowledge(self) -> str:
        """List all stored RAG documents."""
        results = self.recall("", n=100)  # broad recall
        docs: dict[str, dict] = {}
        for r in results:
            meta = r.get("metadata", {})
            if meta.get("type") != "rag_document":
                continue
            title = meta.get("title", "untitled")
            if title not in docs:
                docs[title] = {
                    "title": title,
                    "category": meta.get("category", "general"),
                    "source_url": meta.get("source_url", ""),
                    "chunks": meta.get("total_chunks", "?"),
                    "ingested_at": meta.get("ingested_at", "?"),
                }
        return json.dumps({
            "documents": list(docs.values()),
            "total": len(docs),
        }, indent=2)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RAG Agent — knowledge base Q&A")
    parser.add_argument("--task", type=str, help="Ask a question")
    parser.add_argument("--ingest", type=str, help="URL to ingest into knowledge base")
    parser.add_argument("--title", type=str, default="", help="Title for ingested document")
    parser.add_argument("--category", type=str, default="general", help="Category tag")
    parser.add_argument("--list", action="store_true", help="List all stored documents")
    args = parser.parse_args()

    agent = RAGAgent()

    if args.list:
        print(agent._list_knowledge())
    elif args.ingest:
        result = agent._ingest_url(args.ingest, args.title, args.category)
        print(result)
    elif args.task:
        result = agent.run(args.task)
        print(result["output"])
    else:
        parser.print_help()
