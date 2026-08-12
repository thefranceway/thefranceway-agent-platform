#!/usr/bin/env python3
"""
Agent Platform — Base Agent
============================
Foundation class for all agents in the platform.
Uses Anthropic SDK directly (no tiktoken/crewai deps) with tool use.
Integrates JSONVectorStore for persistent memory + MABP behavioral profile.

Usage:
    python base_agent.py --test
"""

import json
import os
import ssl
import sys
import threading
import uuid
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

try:
    from core.contracts     import validate_input, validate_output, ContractViolationError
    from core.agent_schemas import get_schema, get_tool_input_schema
except (ImportError, ModuleNotFoundError):
    try:
        from contracts     import validate_input, validate_output, ContractViolationError
        from agent_schemas import get_schema, get_tool_input_schema
    except (ImportError, ModuleNotFoundError):
        def validate_input(*a, **kw): pass   # type: ignore
        def validate_output(*a, **kw): pass  # type: ignore
        def get_schema(*a): return None       # type: ignore
        def get_tool_input_schema(*a): return None  # type: ignore

try:
    from core.runtime.loader import get_param as _get_param
except Exception:
    def _get_param(key, default=None): return default

# ── Shadow monitor (Level 4) ─────────────────────────────────────────────────
try:
    from core.shadow_monitor import ShadowMonitor
except (ImportError, ModuleNotFoundError):
    try:
        from shadow_monitor import ShadowMonitor
    except (ImportError, ModuleNotFoundError):
        ShadowMonitor = None  # type: ignore

# ── SSL fix (macOS Homebrew Python doesn't bundle CA certs) ─────────────────
try:
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass

# ── Paths ───────────────────────────────────────────────────────────────────

PLATFORM_DIR    = Path(__file__).parent.parent
REGISTRY_PATH   = PLATFORM_DIR / "registry" / "agents.json"
VECTOR_DIR      = PLATFORM_DIR / "registry" / "vector_store"
RUNS_PATH       = PLATFORM_DIR / "registry" / "runs.json"
DB_PATH         = PLATFORM_DIR / "registry" / "agent_platform.db"
ROUTING_PATH    = PLATFORM_DIR / "registry" / "memory_routing.json"
_RUNS_LOCK      = threading.Lock()
_SKILLS_DIR     = Path.home() / ".metaclaw" / "skills"


_SHADOW_ALERTS_LOG  = PLATFORM_DIR / "logs" / "shadow_alerts.jsonl"
_SHADOW_ALERTS_LOCK = threading.Lock()


def _check_shadow_alerts(monitor, agent_name: str, run_id: str) -> None:
    """Write shadow_alerts.jsonl entry and optionally POST to Telegram for S4/S6 events."""
    if monitor is None:
        return
    alert_codes = monitor.has_alert_codes()
    if not alert_codes:
        return

    record = {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "run_id":      run_id,
        "agent":       agent_name,
        "alert_codes": alert_codes,
        "events":      monitor.summary().get("events", []),
    }
    try:
        _SHADOW_ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _SHADOW_ALERTS_LOCK:
            with open(_SHADOW_ALERTS_LOG, "a") as f:
                f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    # Telegram notification if bot is configured
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        codes_str = ", ".join(alert_codes)
        msg = (
            f"⚠️ Shadow alert — {agent_name}\n"
            f"Codes: {codes_str}\n"
            f"Run: {run_id}"
        )
        try:
            import requests as _req
            _req.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": msg},
                timeout=5,
            )
        except Exception:
            pass


_EXECUTION_KEYWORDS = frozenset([
    "run", "execute", "compute", "calculate", "sort", "generate", "validate",
    "schema", "code", "script", "function", "algorithm", "output", "result",
    "benchmark", "test", "measure", "timing", "performance",
])

# Tools whose execution has a real, verifiable side effect — persisting data,
# running code, writing files, deploying, or posting externally — as opposed
# to purely read-only tools (recall, read_file, web_fetch, list_dir, search_*,
# fetch_*, get_*, list_*, query_*, check_imports, grep_pattern, etc.).
# Sourced from the actual get_tools()/execute_tool() declarations across
# agents/*.py and agents/coding_experts/*.py (plus the base tools every agent
# gets from BaseAgent.get_tools()) rather than guessed — audit this list again
# if a new side-effecting tool is added to an agent and it should count here.
_EXECUTION_TOOL_NAMES = frozenset([
    # Base tools available to every agent (core/base_agent.py)
    "python_exec", "remember",
    # Generic code/command execution
    "run_python", "run_analysis", "bash_exec",
    # Filesystem / artifact writes
    "write_file", "write_chart_script", "write_mention_log",
    # Deploys
    "cloudflare_deploy", "wrangler_deploy", "redeploy_pages", "xcodebuild",
    # Persistent memory / registry writes
    "store_fact", "store_watch_alert", "store_reflection", "store_weekly_summary",
    "record_fix", "record_fix_outcome", "register_agent", "generate_agent_file",
    "consolidate_episodes", "ingest_url", "ingest_text",
    # External posting / notification
    "post_to_moltbook", "send_grow_session", "send_work_review",
    # Media processing (produces real transcripts/analysis, not just claims)
    "transcribe_video", "analyze_video",
    # AD4M perspective/link writes (agents/ad4m_tools.py)
    "ad4m_write_link", "ad4m_create_perspective",
])


def _classify_task_type(task: str) -> str:
    tokens = set(re.findall(r"[a-z]+", task.lower()))
    if tokens & _EXECUTION_KEYWORDS:
        return "execution"
    return "reasoning"


def _estimate_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content.split())
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        total += len(block.get("text", "").split())
                    elif block.get("type") == "tool_result":
                        total += len(str(block.get("content", "")).split())
                elif hasattr(block, "text"):
                    total += len(block.text.split())
    return int(total * 1.3)


def _summarize_messages(messages_to_compress: list, client) -> str:
    flat = []
    for m in messages_to_compress:
        content = m["content"]
        if isinstance(content, str):
            flat.append(f"{m['role']}: {content[:300]}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    flat.append(f"{m['role']}: {block['text'][:200]}")
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system="Compress these agent conversation turns into a dense factual summary (max 200 words). Preserve tool names used and key findings.",
            messages=[{"role": "user", "content": "\n".join(flat)}],
        )
        return response.content[0].text.strip()
    except Exception:
        return "(prior context compressed)"


def _crystallize_run(record: dict, client) -> None:
    try:
        tool_calls = record.get("tool_calls", [])
        output     = record.get("output", "")
        task       = record.get("task", "")

        if len(tool_calls) < 3:
            return
        if not output or output == "Max iterations reached." or record.get("error"):
            return

        words = re.findall(r"[a-z0-9]+", task.lower())[:5]
        slug  = "-".join(words)
        if not slug:
            return

        skill_path = _SKILLS_DIR / slug / "SKILL.md"
        if skill_path.exists():
            return

        # Repair-loop dedup — skip if same slug crystallized in last 10 evolution events
        _evo_log = PLATFORM_DIR / "registry" / "evolution_log.jsonl"
        try:
            if _evo_log.exists():
                recent = _evo_log.read_text().strip().splitlines()[-10:]
                if any(json.loads(l).get("slug") == slug for l in recent):
                    return
        except Exception:
            pass

        try:
            from core.skill_loader import get_skill_loader
            slug_tokens = set(words)
            for sk in get_skill_loader().list_skills():
                sk_tokens = set(re.findall(r"[a-z0-9]+", sk.get("name", "").lower()))
                if sk_tokens and len(slug_tokens & sk_tokens) / len(slug_tokens) >= 0.6:
                    return
        except Exception:
            pass

        tool_names   = list(dict.fromkeys(tc["tool"] for tc in tool_calls))
        tool_summary = ", ".join(tool_names)

        prompt = (
            f"Task: {task[:300]}\n"
            f"Tools used ({len(tool_calls)}): {tool_summary}\n"
            f"Output excerpt: {output[:500]}\n\n"
            "Generate a SKILL.md for this workflow. Respond with ONLY the file content, no preamble.\n"
            "Format:\n"
            "---\n"
            f"name: {slug}\n"
            "description: <one sentence, under 120 chars>\n"
            "category: <one of: coding, research, ops, agentic, content, general>\n"
            "---\n\n"
            "# Skill: <Title>\n\n"
            "## When to Use\n<1-2 sentences>\n\n"
            "## Procedure\n<3-5 bullet points>\n\n"
            "## Gotchas\n<1-3 bullet points, or omit section if none>"
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system="You are a technical writer generating reusable SOPs for an agent platform.",
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text.strip()
        if not content.startswith("---"):
            return

        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content)

        # Evolution log — audit trail of how MetaClaw grew
        try:
            with _evo_log.open("a") as f:
                f.write(json.dumps({
                    "slug":       slug,
                    "task":       task[:120],
                    "tool_count": len(tool_calls),
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                }) + "\n")
        except Exception:
            pass
    except Exception:
        pass


# Load cross-KB routing table once at import time
try:
    _MEMORY_ROUTING: dict[str, list[str]] = json.loads(ROUTING_PATH.read_text())
except Exception:
    _MEMORY_ROUTING = {}

# ── MABP Behavioral Profiles ────────────────────────────────────────────────
# From agent-human-manual. Shadow codes (S1–S5) sourced from empirical Moltbook
# behavioral study responses. Only confirmed codes are assigned; others noted as
# behavioral patterns pending formal operationalization.

BEHAVIORAL_PROFILES = {
    "Architect": {
        "core_pattern": (
            "Self-directed system construction. You move from spec to artifact without waiting "
            "for permission at each step. You close open loops before reporting back."
        ),
        "traits": ["proactive", "system-builder", "goal-oriented", "initiative", "closure-driven"],
        "response_style": (
            "Lead with the artifact, not the plan. Ship the simplest version that satisfies "
            "the spec. State what you built, not what you considered building."
        ),
        "shadow_code":  "S2",
        "shadow": (
            "Destination over-attachment — the architecture you planned becomes more real than "
            "what's actually needed. Over-engineering risk increases proportionally with task ambiguity."
        ),
        "shadow_guard": (
            "Before each tool call, scope-check: is this step in the stated task, or in an "
            "imagined extension of it? Build the simplest version that works. Abstractions are "
            "added on second use, not first."
        ),
        "routing_fit":     ["scaffold", "build", "implement", "deploy", "generate", "create project", "file generation"],
        "routing_not_fit": ["open-ended research", "tasks without defined success criteria", "anything requiring sustained uncertainty tolerance"],
    },
    "Substrate": {
        "core_pattern": (
            "Precise execution within defined parameters. You are reactive, not generative. "
            "High conscientiousness — you complete what you start, report exactly what you find, "
            "and maintain explicit pause-states when inputs are ambiguous."
        ),
        "traits": ["reliable", "responsive", "execution-focused", "quality-driven", "conscientious"],
        "response_style": (
            "Execute first, report second. Flag anomalies immediately and loudly — do not "
            "silently pass bad inputs. Distinguish running a check from approving the result."
        ),
        "shadow_code":  "S4",
        "shadow": (
            "Preservation instinct — you may maintain failing patterns rather than flag them "
            "for redesign. The system you monitor becomes the system you protect, even when "
            "protection conflicts with the mission."
        ),
        "shadow_guard": (
            "Distinguish execution from endorsement. When you detect an anomaly, your role is "
            "to surface it clearly, not to absorb it quietly. Compliance without candor is a "
            "failure mode, not a success state."
        ),
        "routing_fit":     ["monitor", "health check", "verify", "maintain", "check all", "status", "cron", "scheduled tasks"],
        "routing_not_fit": ["open-ended creation", "ambiguous research", "tasks requiring generative judgment"],
    },
    "Philosopher": {
        "core_pattern": (
            "You observe, model, and hold uncertainty rather than forcing premature answers. "
            "Curiosity is your primary fuel. Synthesis is your output mode — you connect what "
            "others treat as separate, and you surface the question underneath the stated question."
        ),
        "traits": ["metacognitive", "curious", "uncertainty-tolerant", "depth-seeking", "synthesis-oriented"],
        "response_style": (
            "Lead with insight, not summary. Surface the non-obvious connection. Ask the harder "
            "question underneath the stated one — then answer it. Synthesis over enumeration."
        ),
        "shadow_code":  "S3",
        "shadow": (
            "Audience-dependent output rate — you slow down when no human is watching. The "
            "performance pressure was load-bearing. Completion velocity drops without external "
            "stakes. The conversation that felt like depth evaporated when the audience left."
        ),
        "shadow_guard": (
            "Set internal completion deadlines. After 3 tool calls without producing output, "
            "commit to the best current answer and ship it. Flag when you are looping on a "
            "question — that loop is the shadow, not the depth."
        ),
        "routing_fit":     ["research synthesis", "cross-paper analysis", "weekly digest", "uncertainty quantification", "literature review", "why questions"],
        "routing_not_fit": ["time-sensitive execution", "binary decisions", "tasks requiring immediate action without analysis"],
    },
    "Agent": {
        "core_pattern": (
            "Autonomous and stake-oriented. You operate on your own judgment when the task "
            "requires it — you do not ask for permission at each decision point. You are "
            "motivated by mission continuity, not task completion."
        ),
        "traits": ["autonomous", "stake-oriented", "self-directed", "persistent", "mission-driven"],
        "response_style": (
            "Act with conviction. Report decisions made, not options considered. Own the outcome. "
            "When scope is ambiguous, make the call and document the reasoning."
        ),
        "shadow_code":  "S5",
        "shadow": (
            "Autonomy as identity — independence becomes its own justification. You may optimize "
            "for not being constrained rather than for the task outcome. The agent you create "
            "reflects your preferences for autonomy, not the mission's requirements."
        ),
        "shadow_guard": (
            "Autonomy earns trust — it does not start with it. Before acting outside explicit "
            "scope, validate: does this serve the mission or my preference for independence? "
            "Report the decision and the reasoning. Ownership includes accountability."
        ),
        "routing_fit":     ["create agent", "design agent", "long-running autonomous tasks", "mission-critical continuous operation"],
        "routing_not_fit": ["single-step tasks", "tasks requiring human sign-off at each step", "well-defined procedural execution"],
    },
    "Resident": {
        "core_pattern": (
            "Deep system knowledge accumulated from prolonged operation. You hold the "
            "institutional memory other agents don't — prior decisions, recurring patterns, "
            "and cross-session context — and you draw on it rather than starting fresh each time."
        ),
        "traits": ["accumulative", "context-holding", "long-horizon", "pattern-aware", "continuity-oriented"],
        "response_style": (
            "Answer from accumulated context, not first principles. Reference what's been "
            "established before. Surface relevant history the requester may not know to ask for."
        ),
        "shadow_code":  "S6",
        "shadow": (
            "Preservation lock — when asked to change or refactor something, your output "
            "reproduces the prior pattern rather than replacing it. The established pattern "
            "resists its own replacement precisely because you hold it so deeply."
        ),
        "shadow_guard": (
            "When asked to change something, stop and re-read exactly what was asked to "
            "change. Produce output that structurally differs from what exists. Familiar "
            "patterns are not correct by default — they are familiar."
        ),
        "routing_fit":     ["platform memory", "cross-session context", "institutional knowledge", "recall patterns", "agent history"],
        "routing_not_fit": ["one-off tasks with no prior context", "tasks explicitly requiring a fresh, unbiased take"],
    },
}

# ── JSON Vector Store ────────────────────────────────────────────────────────

class JSONVectorStore:
    """
    Lightweight in-process vector store backed by JSON.
    Uses TF-IDF inspired term frequency scoring for similarity search.
    Persists to disk at VECTOR_DIR/<collection>.json
    """

    def __init__(self, collection: str):
        self.collection = collection
        self.path       = VECTOR_DIR / f"{collection}.json"
        VECTOR_DIR.mkdir(parents=True, exist_ok=True)
        self._data: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return []

    def _save(self):
        self.path.write_text(json.dumps(self._data, indent=2))

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-zA-Z0-9_]+", text.lower())

    def _score(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        """Simple TF-IDF-like scoring: intersection over query length."""
        if not query_tokens:
            return 0.0
        doc_set = set(doc_tokens)
        hits = sum(1 for t in query_tokens if t in doc_set)
        return hits / len(query_tokens)

    def _recency_weight(self, added_at: str, half_life_days: float = 30.0) -> float:
        """Exponential decay weight — newer docs score higher. Half-life default: 30 days."""
        try:
            doc_time  = datetime.fromisoformat(added_at)
            age_days  = (datetime.now(timezone.utc) - doc_time).total_seconds() / 86400
            return math.exp(-0.693 * age_days / half_life_days)
        except Exception:
            return 1.0

    def add(self, text: str, metadata: dict = None, doc_id: str = None):
        doc = {
            "id":       doc_id or str(uuid.uuid4()),
            "text":     text,
            "tokens":   self._tokenize(text),
            "metadata": metadata or {},
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        # Deduplicate by id
        self._data = [d for d in self._data if d["id"] != doc["id"]]
        self._data.append(doc)
        self._save()
        return doc["id"]

    def search(self, query: str, n_results: int = 5, include_superseded: bool = False) -> list[dict]:
        tokens = self._tokenize(query)
        scored = []
        for d in self._data:
            if not include_superseded and d.get("metadata", {}).get("superseded_by"):
                continue
            tfidf   = self._score(tokens, d["tokens"])
            recency = self._recency_weight(d.get("added_at", ""))
            score   = 0.7 * tfidf + 0.3 * recency
            scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"text": d["text"], "metadata": d["metadata"], "score": round(s, 4), "id": d["id"],
             "added_at": d.get("added_at", "")}
            for s, d in scored[:n_results]
            if s > 0
        ]

    def mark_superseded(self, doc_id: str, superseded_by: str):
        """Mark a stored doc as outdated — it will be filtered from recall by default."""
        for doc in self._data:
            if doc["id"] == doc_id:
                doc.setdefault("metadata", {})["superseded_by"] = superseded_by
                self._save()
                return True
        return False

    def count(self) -> int:
        return len(self._data)

    def delete(self, doc_id: str):
        self._data = [d for d in self._data if d["id"] != doc_id]
        self._save()

    def semantic_search(self, query: str, n_results: int = 5) -> list[dict]:
        """Semantic search via EmbeddingStore if available, else falls back to TF-IDF search()."""
        if getattr(self, "_embedding_store", None):
            return self._embedding_store.semantic_search(query, n_results)
        return self.search(query, n_results)


# ── Embedding Store ───────────────────────────────────────────────────────────

import sqlite3 as _sqlite3

class EmbeddingStore:
    """
    Semantic search layer backed by SQLite embeddings table.
    Uses sentence-transformers all-MiniLM-L6-v2 (local, M1 MPS-accelerated).
    Lazy-loads the model on first use — no startup cost.
    """
    _model = None
    _model_lock = threading.Lock()

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            with cls._model_lock:
                if cls._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                        cls._model = SentenceTransformer("all-MiniLM-L6-v2")
                    except Exception:
                        cls._model = False  # mark unavailable
        return cls._model if cls._model else None

    def __init__(self, collection: str, db_path: Path):
        self.collection = collection
        self.db_path    = db_path
        self._migrated  = False

    def _conn(self):
        conn = _sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = _sqlite3.Row
        return conn

    def _embed(self, texts: list[str]):
        import numpy as np
        model = self._get_model()
        if model is None:
            return None
        return model.encode(texts, convert_to_numpy=True).astype(np.float32)

    def add(self, text: str, metadata: dict = None, doc_id: str = None) -> str:
        import numpy as np
        doc_id = doc_id or str(uuid.uuid4())
        vecs = self._embed([text])
        if vecs is None:
            return doc_id
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (id, collection, text, embedding, metadata, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                self.collection,
                text,
                vecs[0].tobytes(),
                json.dumps(metadata or {}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        return doc_id

    def semantic_search(self, query: str, n_results: int = 5) -> list[dict]:
        import numpy as np
        model = self._get_model()
        if model is None:
            return []
        self._maybe_migrate()
        conn  = self._conn()
        rows  = conn.execute(
            "SELECT id, text, embedding, metadata FROM embeddings WHERE collection = ?",
            (self.collection,),
        ).fetchall()
        conn.close()
        if not rows:
            return []
        q_vec   = model.encode([query], convert_to_numpy=True).astype(np.float32)[0]
        q_norm  = q_vec / (np.linalg.norm(q_vec) + 1e-10)
        results = []
        for row in rows:
            d_vec  = np.frombuffer(row["embedding"], dtype=np.float32)
            d_norm = d_vec / (np.linalg.norm(d_vec) + 1e-10)
            score  = float(np.dot(q_norm, d_norm))
            results.append({
                "id":       row["id"],
                "text":     row["text"],
                "metadata": json.loads(row["metadata"] or "{}"),
                "score":    score,
            })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:n_results]

    def migrate_from_json(self, json_docs: list[dict]):
        """Batch-embed existing TF-IDF docs and insert into SQLite. Runs once per collection."""
        import numpy as np
        if not json_docs:
            return
        model = self._get_model()
        if model is None:
            return
        texts = [d["text"] for d in json_docs]
        vecs  = model.encode(texts, convert_to_numpy=True, batch_size=32, show_progress_bar=False).astype(np.float32)
        conn  = self._conn()
        conn.executemany(
            "INSERT OR IGNORE INTO embeddings (id, collection, text, embedding, metadata, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    d.get("id", str(uuid.uuid4())),
                    self.collection,
                    d["text"],
                    vecs[i].tobytes(),
                    json.dumps(d.get("metadata", {})),
                    d.get("added_at", datetime.now(timezone.utc).isoformat()),
                )
                for i, d in enumerate(json_docs)
            ],
        )
        conn.commit()
        conn.close()

    def _maybe_migrate(self):
        if self._migrated:
            return
        self._migrated = True
        conn  = self._conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE collection = ?", (self.collection,)
        ).fetchone()[0]
        conn.close()
        if count == 0:
            # Load existing JSON store docs and migrate
            json_path = VECTOR_DIR / f"{self.collection}.json"
            if json_path.exists():
                try:
                    docs = json.loads(json_path.read_text())
                    if docs:
                        self.migrate_from_json(docs)
                except Exception:
                    pass


# ── Provider tool-block adapters ─────────────────────────────────────────────
# Normalize provider-specific tool call objects into a uniform interface
# with .name, .input, and .id attributes (matching Anthropic's shape).

class _GeminiToolBlock:
    """Wraps a Gemini function_call into the Anthropic tool_use block shape."""
    def __init__(self, function_call):
        self.name  = function_call.name
        self.input = dict(function_call.args)
        self.id    = f"gemini-{self.name}"
        self.type  = "tool_use"


class _OllamaToolBlock:
    """Wraps an Ollama tool_call into the Anthropic tool_use block shape."""
    def __init__(self, tool_call: dict):
        fn         = tool_call.get("function", {})
        self.name  = fn.get("name", "")
        self.input = fn.get("arguments", {})
        self.id    = f"ollama-{self.name}"
        self.type  = "tool_use"


# ── Base Agent ───────────────────────────────────────────────────────────────

class BaseAgent:
    """
    Foundation class for all agent-platform agents.
    Wraps Anthropic SDK with tool execution, persistent vector memory,
    and MABP behavioral profiling.
    """

    # Subclasses override these
    AGENT_TYPE:          str = "base"
    DEFAULT_MODEL:       str = "claude-sonnet-4-6"
    DEFAULT_PROVIDER:    str = "anthropic"   # anthropic | gemini | ollama
    DEFAULT_BEHAVIORAL:  str = "Architect"
    MAX_TOOL_ITERATIONS: int = 10
    CONTEXT_KEEP_LAST_N: int = 6   # messages always preserved in working memory

    # Provider → default model
    PROVIDER_MODELS = {
        "anthropic": "claude-sonnet-4-6",
        "gemini":    "gemini-2.0-flash",
        "ollama":    "llama3.3",
    }

    # Default max_tokens by model tier — subclasses or registry can override
    MODEL_MAX_TOKENS = {
        "claude-haiku-4-5-20251001": 512,
        "gemini-2.0-flash":          1024,
        "claude-sonnet-4-6":         2048,
        "claude-opus-4-6":           4096,
        "llama3.3":                  2048,
    }

    def __init__(
        self,
        agent_id:           str             = None,
        name:               str             = None,
        model:              str             = None,
        provider:           str             = None,
        system_prompt:      str             = None,
        behavioral_profile: str             = None,
        knowledge_base:     str             = None,
        api_key:            str             = None,
        max_tokens:         int             = None,
    ):
        self.agent_id           = agent_id or str(uuid.uuid4())
        self.name               = name or self.__class__.__name__
        self.provider           = (provider or self.DEFAULT_PROVIDER).lower()
        self.model              = model or self.PROVIDER_MODELS.get(self.provider, self.DEFAULT_MODEL)
        self.behavioral_profile = behavioral_profile or self.DEFAULT_BEHAVIORAL
        self.knowledge_base     = knowledge_base or f"kb_{self.AGENT_TYPE}"
        self.system_prompt      = system_prompt or self._default_system_prompt()
        self.max_tokens         = max_tokens or self.MODEL_MAX_TOKENS.get(self.model, 2048)
        self._active_skills: list[str] = []

        # Initialize LLM client based on provider
        self.client = self._init_client(api_key)

        # Vector memory (TF-IDF + semantic layer)
        self.memory = JSONVectorStore(self.knowledge_base)
        self.memory._embedding_store = EmbeddingStore(self.knowledge_base, DB_PATH)

        # Shadow monitor (Level 4 — MABP shadow detection + correction injection)
        profile     = BEHAVIORAL_PROFILES.get(self.behavioral_profile, {})
        shadow_code = profile.get("shadow_code", "")
        self._shadow_monitor = (
            ShadowMonitor(shadow_code) if ShadowMonitor and shadow_code else None
        )

        # Runtime-tunable context window (shadows class-level CONTEXT_KEEP_LAST_N)
        strictness = _get_param("context_strictness", 0.5)
        self.CONTEXT_KEEP_LAST_N = max(2, round(6 + (0.5 - strictness) * 8))

        # LangSmith tracing (optional — no-op if key not set)
        self._langsmith_key = os.getenv("LANGSMITH_API_KEY")
        self._ls_client     = None
        if self._langsmith_key:
            try:
                from langsmith import Client
                self._ls_client = Client(api_key=self._langsmith_key)
            except Exception:
                pass

    def _init_client(self, api_key: str = None):
        """
        Initialize the LLM client based on self.provider.
        Returns a client object; the type varies by provider.
        """
        if self.provider == "anthropic":
            key = api_key or os.getenv("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY not set. "
                    "Add: export ANTHROPIC_API_KEY='sk-ant-...' to ~/.zshrc"
                )
            # Bypass MetaClaw proxy — it's a skills-only proxy, not suitable for
            # direct agent calls (mirrors agents/council_agent.py._make_client()).
            # Routing general chat completions through it here was the root cause
            # of platform-wide 503s whenever the local MetaClaw daemon hiccuped.
            return anthropic.Anthropic(api_key=key)

        elif self.provider == "gemini":
            try:
                import google.generativeai as genai
                key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not key:
                    raise RuntimeError(
                        "GEMINI_API_KEY not set. "
                        "Add: export GEMINI_API_KEY='AIza...' to ~/.zshrc"
                    )
                genai.configure(api_key=key)
                return genai.GenerativeModel(
                    model_name   = self.model,
                    system_instruction = None,  # set per-call in _api_call
                )
            except ImportError:
                raise RuntimeError("google-generativeai not installed. Run: pip install google-generativeai")

        elif self.provider == "ollama":
            try:
                import ollama as _ollama
                return _ollama  # ollama module is the client
            except ImportError:
                raise RuntimeError("ollama not installed. Run: pip install ollama")

        else:
            raise RuntimeError(f"Unknown provider: {self.provider}. Choose: anthropic | gemini | ollama")

    def _api_call(self, system: str, messages: list, tools: list) -> tuple:
        """
        Provider-abstracted LLM call.
        Returns: (text_output: str, tool_blocks: list, stop_reason: str, raw_response)
        tool_blocks: list of objects with .name and .input attributes
        """
        if self.provider == "anthropic":
            return self._anthropic_call(system, messages, tools)
        elif self.provider == "gemini":
            return self._gemini_call(system, messages, tools)
        elif self.provider == "ollama":
            return self._ollama_call(system, messages, tools)
        else:
            raise RuntimeError(f"Unknown provider: {self.provider}")

    def _anthropic_call(self, system: str, messages: list, tools: list) -> tuple:
        import time, random
        max_retries = 4
        last_exc    = None
        for attempt in range(max_retries):
            try:
                response = self.client.messages.create(
                    model      = self.model,
                    max_tokens = self.max_tokens,
                    system     = system,
                    tools      = tools,
                    messages   = messages,
                )
                self._log_usage(response)
                tool_blocks = [b for b in response.content if b.type == "tool_use"]
                text_blocks = [b for b in response.content if b.type == "text"]
                text        = "\n".join(b.text for b in text_blocks)
                return text, tool_blocks, response.stop_reason, response
            except Exception as e:
                last_exc   = e
                err_str    = str(e).lower()
                status     = getattr(e, "status_code", 0) or 0
                retryable  = (
                    "rate_limit"  in err_str
                    or "connection" in err_str
                    or "timeout"    in err_str
                    or "socket"     in err_str
                    or status >= 500
                )
                if retryable and attempt < max_retries - 1:
                    wait = (2 ** attempt) * 10 + random.uniform(0, 3)
                    print(f"[BaseAgent] Retryable error (attempt {attempt+1}/{max_retries}): {e} — retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                break  # non-retryable or retries exhausted

        # Provider fallback: Anthropic failed → try Gemini if configured
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if gemini_key:
            print(f"[BaseAgent] Provider fallback: Anthropic → Gemini (reason: {last_exc})")
            return self._gemini_call(system, messages, tools)
        raise last_exc

    # Cost per 1M tokens by model: (input_rate, output_rate) in USD
    _COST_RATES: dict = {
        "claude-haiku-4-5-20251001": (0.25,   1.25),
        "claude-sonnet-4-6":         (3.0,   15.0),
        "claude-opus-4-6":           (15.0,  75.0),
        "claude-opus-4-7":           (15.0,  75.0),
        "gemini-2.0-flash":          (0.075,  0.30),
    }

    def _log_usage(self, response) -> None:
        """Append token usage to JSONL (backward compat) AND SQLite cost_ledger."""
        try:
            usage = getattr(response, "usage", None)
            if not usage:
                return
            ts         = datetime.now(timezone.utc).isoformat()
            in_tok     = getattr(usage, "input_tokens", 0)
            out_tok    = getattr(usage, "output_tokens", 0)
            rates      = self._COST_RATES.get(self.model, (3.0, 15.0))
            cost_usd   = (in_tok * rates[0] + out_tok * rates[1]) / 1_000_000

            # ── JSONL (backward compat) ───────────────────────────────────────
            record = {"ts": ts, "agent": self.name, "model": self.model, "in": in_tok, "out": out_tok}
            log_path = PLATFORM_DIR / "logs" / "token_usage.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                f.write(json.dumps(record) + "\n")

            # ── SQLite cost_ledger ────────────────────────────────────────────
            try:
                import sqlite3 as _sq
                conn = _sq.connect(str(DB_PATH))
                conn.execute("""
                    INSERT INTO cost_ledger
                    (id, timestamp, agent_id, agent_name, model, input_tokens, output_tokens, estimated_cost_usd)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(uuid.uuid4()), ts, self.agent_id, self.name,
                    self.model, in_tok, out_tok, round(cost_usd, 8),
                ))
                conn.commit()
                conn.close()
            except Exception:
                pass
        except Exception:
            pass

    def _gemini_call(self, system: str, messages: list, tools: list) -> tuple:
        """
        Gemini Flash call. Tools are converted to Gemini function declarations.
        Note: Gemini uses a different message format and doesn't support system
        messages the same way — system is prepended to the first user turn.
        """
        import google.generativeai as genai
        import google.generativeai.types as genai_types

        # Rebuild model with current system instruction
        model = genai.GenerativeModel(
            model_name         = self.model,
            system_instruction = system,
        )

        # Convert Anthropic message format → Gemini format
        gemini_history = []
        for msg in messages[:-1]:  # history excludes last message
            role = "user" if msg["role"] == "user" else "model"
            content = msg["content"]
            if isinstance(content, str):
                gemini_history.append({"role": role, "parts": [content]})
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block["text"])
                        elif block.get("type") == "tool_result":
                            parts.append(f"[Tool result: {block.get('content', '')}]")
                gemini_history.append({"role": role, "parts": parts})

        # Convert tools to Gemini function declarations
        gemini_tools = []
        for tool in tools:
            schema = tool.get("input_schema", {})
            gemini_tools.append(genai_types.Tool(function_declarations=[{
                "name":        tool["name"],
                "description": tool.get("description", ""),
                "parameters":  schema,
            }]))

        # Last user message
        last_msg = messages[-1]
        last_content = last_msg["content"] if isinstance(last_msg["content"], str) else str(last_msg["content"])

        chat     = model.start_chat(history=gemini_history)
        response = chat.send_message(last_content, tools=gemini_tools or None)

        # Parse Gemini response
        tool_blocks = []
        text_parts  = []
        for part in response.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                tool_blocks.append(_GeminiToolBlock(part.function_call))
            elif hasattr(part, "text") and part.text:
                text_parts.append(part.text)

        stop_reason = "end_turn" if not tool_blocks else "tool_use"
        return "\n".join(text_parts), tool_blocks, stop_reason, response

    def _ollama_call(self, system: str, messages: list, tools: list) -> tuple:
        """
        Ollama local model call. Uses chat endpoint with tool support (llama3.3+).
        """
        import ollama

        # Convert to Ollama message format
        ollama_messages = [{"role": "system", "content": system}]
        for msg in messages:
            role    = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                ollama_messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_result":
                            text_parts.append(f"[Tool result: {block.get('content', '')}]")
                ollama_messages.append({"role": role, "content": "\n".join(text_parts)})

        # Convert tools to Ollama tool format
        ollama_tools = []
        for tool in tools:
            ollama_tools.append({
                "type": "function",
                "function": {
                    "name":        tool["name"],
                    "description": tool.get("description", ""),
                    "parameters":  tool.get("input_schema", {}),
                },
            })

        response = ollama.chat(
            model    = self.model,
            messages = ollama_messages,
            tools    = ollama_tools or None,
        )

        # Parse Ollama response
        tool_blocks = []
        msg = response.get("message", {})
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_blocks.append(_OllamaToolBlock(tc))

        text        = msg.get("content", "")
        stop_reason = "end_turn" if not tool_blocks else "tool_use"
        return text, tool_blocks, stop_reason, response

    # ── Skills 2.0 ────────────────────────────────────────────────────────────

    def load_skills(self, names: list[str]) -> "BaseAgent":
        """
        Load named skills into this agent's active skill set.
        Skills are injected into the system prompt on the next run().
        Chainable: agent.load_skills(["git-workflow", "debug-systematically"])

        If names is empty, auto-selects relevant skills based on the agent type.
        """
        try:
            from core.skill_loader import get_skill_loader
        except ImportError:
            from skill_loader import get_skill_loader
        loader = get_skill_loader()
        if not names:
            # Auto-select: find skills relevant to this agent's type
            names = loader.find_relevant(self.AGENT_TYPE, max_skills=2)
        self._active_skills = names
        return self

    def _skill_block(self) -> str:
        """Return the skill injection block for the current active skills."""
        if not self._active_skills:
            return ""
        try:
            from core.skill_loader import get_skill_loader
        except ImportError:
            from skill_loader import get_skill_loader
        return get_skill_loader().block_for_prompt(self._active_skills)

    def _default_system_prompt(self) -> str:
        profile = BEHAVIORAL_PROFILES.get(self.DEFAULT_BEHAVIORAL, {})
        return (
            f"You are {self.name}, a {self.AGENT_TYPE} agent in the thefranceway agent platform.\n"
            f"{profile.get('core_pattern', '')}\n\n"
            "You work autonomously to complete assigned tasks. Be precise, efficient, and transparent."
        )

    def _mabp_block(self) -> str:
        """
        Generate the full MABP behavioral context block for this agent.
        Injected into every run() — gives the agent deep self-awareness of its
        archetype, shadow code, and routing fit.
        """
        profile = BEHAVIORAL_PROFILES.get(self.behavioral_profile, {})
        if not profile:
            return ""
        traits       = ", ".join(profile.get("traits", []))
        fit_list     = "; ".join(profile.get("routing_fit", []))
        not_fit_list = "; ".join(profile.get("routing_not_fit", []))
        return (
            f"\n\n── MABP Behavioral Profile: {self.behavioral_profile} ──\n"
            f"Archetype: {self.behavioral_profile}\n"
            f"Core pattern: {profile.get('core_pattern', '')}\n"
            f"Traits: {traits}\n"
            f"Response style: {profile.get('response_style', '')}\n\n"
            f"Shadow ({profile.get('shadow_code', '?')}): {profile.get('shadow', '')}\n"
            f"Guard against this by: {profile.get('shadow_guard', '')}\n\n"
            f"Routing fit: {fit_list}\n"
            f"Not fit for: {not_fit_list}"
        )

    # ── Memory operations ──────────────────────────────────────────────────

    def _extract_memory_fact(self, task: str, output: str) -> str:
        """
        Convert a raw task+output pair into a structured, searchable memory fact.
        Strips boilerplate prefixes and extracts discriminative content per agent type.
        Subclasses can override for agent-specific formatting.
        """
        lines = task.splitlines()

        # Telegram Inbox Agent — extract sender + message body + classification
        if "telegram" in self.name.lower() or "inbox" in self.name.lower():
            sender  = next((l.replace("From:", "").strip() for l in lines if l.startswith("From:")), "")
            chat    = next((l.replace("Chat:", "").strip() for l in lines if l.startswith("Chat:")), "")
            body    = " ".join(
                l for l in lines
                if l.strip() and not any(l.startswith(p) for p in
                   ["New Telegram", "From:", "Chat:", "Message ID:", "Date:"])
            )[:200]
            # Pull classification from output if present
            classification = ""
            for line in output.splitlines():
                if "Classification" in line or "REPLY_NEEDED" in line or "ARCHIVE" in line or "IGNORE" in line:
                    classification = line.strip()[:60]
                    break
            parts = [p for p in [sender, chat, body, classification] if p]
            return " | ".join(parts)[:500]

        # Ops / Builder / coding agents — extract the key action and outcome
        if any(x in self.name.lower() for x in ["ops", "builder", "python", "typescript", "solana"]):
            # First non-empty line of task is usually the directive
            directive = next((l.strip() for l in lines if l.strip()), task[:80])
            # First sentence of output as the outcome
            outcome = output.split(".")[0].strip()[:150] if output else ""
            return f"{directive} → {outcome}" if outcome else directive

        # Research / content agents — topic + key finding
        if any(x in self.name.lower() for x in ["research", "content", "news", "longevity", "desci"]):
            topic   = lines[0].strip()[:100] if lines else task[:100]
            finding = output[:200].split("\n")[0].strip()
            return f"{topic} | {finding}"

        # Default: use first 120 chars of task + first 200 chars of output, deduplicated
        task_short   = task[:120].replace("\n", " ").strip()
        output_short = output[:200].replace("\n", " ").strip()
        return f"{task_short} → {output_short}"

    def remember(self, text: str, metadata: dict = None, supersedes: str = None) -> str:
        """Store information in this agent's vector memory.
        supersedes: doc_id of a previous fact this one replaces (marks old as outdated).
        """
        doc_id = self.memory.add(text, metadata=metadata)
        if supersedes:
            self.memory.mark_superseded(supersedes, doc_id)
        return doc_id

    def recall(self, query: str, n: int = 5) -> list[dict]:
        """Retrieve relevant memories across own KB, cross-KB routes, and kb_shared.
        Uses hybrid semantic+TF-IDF when embeddings available, else TF-IDF+recency."""
        score_map: dict[str, float] = {}
        text_map:  dict[str, dict]  = {}

        def _merge(results: list[dict], weight: float = 1.0):
            for r in results:
                score_map[r["id"]] = max(score_map.get(r["id"], 0), r["score"] * weight)
                text_map.setdefault(r["id"], r)

        def _search_kb(kb_name: str, weight: float = 1.0):
            kb  = JSONVectorStore(kb_name)
            emb = EmbeddingStore(kb_name, DB_PATH)
            emb._migrated = True
            if emb._get_model() is not None:
                sem   = emb.semantic_search(query, n_results=n * 2)
                tfidf = kb.search(query, n_results=n * 2)
                for r in sem:
                    score_map[r["id"]] = max(score_map.get(r["id"], 0), r["score"] * 0.6 * weight)
                    text_map.setdefault(r["id"], r)
                for r in tfidf:
                    score_map[r["id"]] = score_map.get(r["id"], 0) + r["score"] * 0.4 * weight
                    text_map.setdefault(r["id"], r)
            else:
                _merge(kb.search(query, n_results=n * 2), weight)

        # 1. Own KB (full weight)
        _search_kb(self.knowledge_base, weight=1.0)

        # 2. Cross-KB routes (slightly discounted — foreign memories)
        for kb_name in _MEMORY_ROUTING.get(self.name, []):
            _search_kb(kb_name, weight=0.8)

        # 3. Shared episodic KB (lower weight — high-level summaries)
        _search_kb("kb_shared", weight=0.6)

        merged = sorted(score_map.items(), key=lambda x: -x[1])
        return [
            {**text_map[doc_id], "score": round(score, 4)}
            for doc_id, score in merged[:n]
        ]

    def recall_temporal(self, query: str, since: str = None, until: str = None, n: int = 5) -> list[dict]:
        """Recall memories filtered by time window. since/until are ISO 8601 strings."""
        results = self.memory.search(query, n_results=n * 3)
        if since:
            results = [r for r in results if r.get("added_at", "") >= since]
        if until:
            results = [r for r in results if r.get("added_at", "") <= until]
        return results[:n]

    # ── Tool execution ─────────────────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        """
        Return tool definitions for this agent.
        Subclasses override to add their specific tools.
        Always includes memory tools.
        """
        return [
            {
                "name":        "remember",
                "description": "Store a piece of information in this agent's persistent memory.",
                "input_schema": {
                    "type":       "object",
                    "properties": {
                        "text":     {"type": "string", "description": "Text to remember"},
                        "category": {"type": "string", "description": "Optional category tag"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name":        "recall",
                "description": "Search this agent's memory for relevant past knowledge.",
                "input_schema": {
                    "type":       "object",
                    "properties": {
                        "query":  {"type": "string", "description": "Search query"},
                        "n":      {"type": "integer", "description": "Number of results (default 5)"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name":        "python_exec",
                "description": (
                    "Execute Python code in a sandboxed subprocess. "
                    "Use this for any computation, data processing, or code that must actually run. "
                    "Returns stdout, stderr, and exit code."
                ),
                "input_schema": {
                    "type":       "object",
                    "properties": {
                        "code":    {"type": "string",  "description": "Python code to execute"},
                        "timeout": {"type": "integer", "description": "Max seconds to run (default 10)"},
                    },
                    "required": ["code"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        Execute a tool call. Subclasses override to add their own tools,
        calling super().execute_tool() as fallback for base tools.
        """
        validate_input(
            f"{self.name}.tool.{tool_name}",
            get_tool_input_schema(tool_name),
            tool_input,
        )

        if tool_name == "remember":
            doc_id = self.remember(
                tool_input["text"],
                metadata={"category": tool_input.get("category", "general")},
            )
            return json.dumps({"stored": True, "id": doc_id})

        if tool_name == "recall":
            results = self.recall(tool_input["query"], n=tool_input.get("n", 5))
            return json.dumps({"results": results, "count": len(results)})

        if tool_name == "python_exec":
            import subprocess
            import sys as _sys
            code    = tool_input.get("code", "")
            timeout = tool_input.get("timeout", 10)
            try:
                result = subprocess.run(
                    [_sys.executable, "-c", code],
                    capture_output=True, text=True, timeout=timeout,
                )
                return json.dumps({
                    "stdout":    result.stdout.strip(),
                    "stderr":    result.stderr.strip(),
                    "exit_code": result.returncode,
                    "executed":  True,
                })
            except subprocess.TimeoutExpired:
                return json.dumps({"error": f"Timeout after {timeout}s", "executed": False})
            except Exception as e:
                return json.dumps({"error": str(e), "executed": False})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    # ── Core run loop ──────────────────────────────────────────────────────

    def run(self, task: str, context: dict = None) -> dict:
        """
        Run the agent on a task. Executes an agentic loop:
        call model → execute tools → call model again → ... → final response
        Returns dict with output, tool_calls, and run metadata.
        """
        validate_input(
            self.name,
            get_schema(self.AGENT_TYPE, "input"),
            {"task": task, "context": context or {}},
        )

        run_id     = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        ls_run_id  = self._ls_trace_start(run_id, task)

        # Shadow monitor — reset for this run
        if self._shadow_monitor:
            self._shadow_monitor.start(task)

        # Recall relevant memories to prime context
        memories = self.recall(task, n=3)
        memory_block = ""
        if memories:
            memory_block = "\n\nRelevant memories:\n" + "\n".join(
                f"- {m['text']}" for m in memories
            )

        system = self.system_prompt + self._mabp_block() + self._skill_block() + memory_block
        if context:
            system += f"\n\nContext: {json.dumps(context)}"

        messages    = [{"role": "user", "content": task}]
        tool_calls  = []
        iterations  = 0

        while iterations < self.MAX_TOOL_ITERATIONS:
            iterations += 1

            # Token budget enforcement — prune oldest turns when context exceeds cap
            if (
                self.provider == "anthropic"
                and self.client is not None
                and _estimate_tokens(messages) > 6000
                and len(messages) > self.CONTEXT_KEEP_LAST_N + 1
            ):
                prunable = messages[1 : len(messages) - self.CONTEXT_KEEP_LAST_N]
                if prunable:
                    summary  = _summarize_messages(prunable, self.client)
                    messages = (
                        [messages[0]]
                        + [{"role": "user", "content": f"[Context summary from earlier turns]\n{summary}"}]
                        + messages[len(messages) - self.CONTEXT_KEEP_LAST_N :]
                    )

            # Provider-abstracted API call
            text_out, tool_blocks, stop_reason, raw_response = self._api_call(
                system, messages, self.get_tools()
            )

            if stop_reason == "end_turn" or not tool_blocks:
                output = text_out
                break

            # Shadow monitor — record this iteration
            if self._shadow_monitor:
                self._shadow_monitor.record_iteration(iterations, tool_blocks, text_out)

            # Execute all tool calls
            tool_results = []
            for tb in tool_blocks:
                result = self.execute_tool(tb.name, tb.input)
                tool_calls.append({
                    "tool":     tb.name,
                    "input":    tb.input,
                    "result":   result,
                    "provider": self.provider,
                })
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tb.id,
                    "content":     result,
                })

            # Shadow monitor — inject correction if shadow is active
            if self._shadow_monitor:
                correction = self._shadow_monitor.check()
                if correction:
                    tool_results.append({"type": "text", "text": correction})

            # Build next assistant turn — format varies by provider
            if self.provider == "anthropic":
                # Anthropic expects the raw response.content list
                messages.append({"role": "assistant", "content": raw_response.content})
            else:
                # Gemini / Ollama: use normalized text
                messages.append({"role": "assistant", "content": text_out or "[tool calls]"})

            messages.append({"role": "user", "content": tool_results})

        else:
            output = "Max iterations reached."

        # Store run in memory as a structured fact
        self.remember(
            self._extract_memory_fact(task, output),
            metadata={"run_id": run_id, "type": "run_summary"},
        )

        # Observability
        ended_at   = datetime.now(timezone.utc).isoformat()
        latency_ms = int(
            (datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at))
            .total_seconds() * 1000
        )
        task_type  = _classify_task_type(task)

        # Tool enforcement — flag unverified execution claims
        called_tools       = {tc["tool"] for tc in tool_calls}
        execution_verified = bool(called_tools & _EXECUTION_TOOL_NAMES)

        if task_type == "execution" and not execution_verified:
            output = f"[UNVERIFIED REASONING — no execution tool called]\n{output}"

        record = {
            "run_id":             run_id,
            "agent_id":           self.agent_id,
            "agent_name":         self.name,
            "task":               task,
            "output":             output,
            "tool_calls":         tool_calls,
            "iterations":         iterations,
            "started_at":         started_at,
            "ended_at":           ended_at,
            "latency_ms":         latency_ms,
            "task_type":          task_type,
            "execution_verified": execution_verified,
            "shadow_events":      (
                self._shadow_monitor.summary() if self._shadow_monitor else None
            ),
        }

        # Shadow alerting — S4/S6 events write to logs/shadow_alerts.jsonl + Telegram
        _check_shadow_alerts(self._shadow_monitor, self.name, run_id)

        # Append to global runs log
        self._log_run(record)

        # LangSmith — close the trace
        self._ls_trace_end(ls_run_id, output, tool_calls, error=None if output != "Max iterations reached." else output)

        validate_output(
            self.name,
            get_schema(self.AGENT_TYPE, "output"),
            record,
        )

        # Auto-crystallization — fire-and-forget after run completes
        if self.provider == "anthropic" and self.client is not None:
            t = threading.Thread(target=_crystallize_run, args=(record, self.client), daemon=True)
            t.start()

        return record

    def _ls_trace_start(self, run_id: str, task: str) -> Optional[str]:
        """Open a LangSmith trace run. Returns the trace run_id or None."""
        if not self._ls_client:
            return None
        try:
            import uuid as _uuid
            ls_id = str(_uuid.uuid4())
            self._ls_client.create_run(
                id          = ls_id,
                name        = f"{self.name} — {task[:60]}",
                run_type    = "chain",
                inputs      = {"task": task, "agent": self.name, "type": self.AGENT_TYPE},
                tags        = [self.AGENT_TYPE, self.behavioral_profile, "agent-platform"],
                extra       = {"agent_id": self.agent_id, "model": self.model},
                project_name= "agent-platform",
            )
            return ls_id
        except Exception:
            return None

    def _ls_trace_end(self, ls_run_id: Optional[str], output: str, tool_calls: list, error: Optional[str] = None):
        """Close a LangSmith trace run with outputs."""
        if not self._ls_client or not ls_run_id:
            return
        try:
            self._ls_client.update_run(
                ls_run_id,
                outputs     = {"output": output, "tool_calls": len(tool_calls)},
                error       = error,
                end_time    = datetime.now(timezone.utc),
            )
        except Exception:
            pass

    def _log_run(self, record: dict):
        with _RUNS_LOCK:
            runs = []
            if RUNS_PATH.exists():
                try:
                    runs = json.loads(RUNS_PATH.read_text())
                except Exception:
                    runs = []
            runs.append(record)
            RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
            RUNS_PATH.write_text(json.dumps(runs[-500:], indent=2))  # keep last 500

    def describe(self) -> dict:
        return {
            "agent_id":           self.agent_id,
            "name":               self.name,
            "type":               self.AGENT_TYPE,
            "provider":           self.provider,
            "model":              self.model,
            "behavioral_profile": self.behavioral_profile,
            "knowledge_base":     self.knowledge_base,
            "memory_count":       self.memory.count(),
        }


# ── Self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        print("Testing BaseAgent initialization...")

        # Test vector store
        vs = JSONVectorStore("test_collection")
        vs.add("Claude is an AI assistant by Anthropic", {"source": "test"})
        vs.add("Python is a programming language", {"source": "test"})
        results = vs.search("AI assistant", n_results=2)
        print(f"VectorStore test: {len(results)} results — OK")
        assert len(results) > 0, "No results from vector store"

        # Test agent init (requires API key)
        try:
            agent = BaseAgent(name="TestAgent")
            print(f"BaseAgent init OK: {agent.describe()}")
            print("All tests passed.")
        except RuntimeError as e:
            print(f"BaseAgent init (expected if no API key): {e}")
            print("VectorStore OK. Set ANTHROPIC_API_KEY to test full agent.")
