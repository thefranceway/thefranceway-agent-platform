#!/usr/bin/env python3
"""
Agent Platform — Agent Registry
=================================
Load, save, and query agent definitions from registry/agents.json.
Syncs to SQLite (local D1 equivalent) for structured queries.

Agent spec format:
{
  "id":                 "uuid",
  "name":               "Python Expert",
  "type":               "coding_expert",
  "model":              "claude-sonnet-4-6",
  "system_prompt":      "...",
  "tools":              ["bash", "read", "write", ...],
  "knowledge_base":     "chromadb_collection_python",
  "behavioral_profile": "Architect",
  "created_by":         "meta-agent" | "user",
  "created_at":         "2026-02-27",
  "enabled":            true
}
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────────────────────

PLATFORM_DIR  = Path(__file__).parent.parent
REGISTRY_PATH = PLATFORM_DIR / "registry" / "agents.json"
DB_PATH       = PLATFORM_DIR / "registry" / "agent_platform.db"

# ── SQLite helpers ────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            id                 TEXT PRIMARY KEY,
            name               TEXT NOT NULL,
            type               TEXT NOT NULL,
            model              TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
            system_prompt      TEXT,
            tools              TEXT,          -- JSON array
            knowledge_base     TEXT,
            behavioral_profile TEXT,
            created_by         TEXT DEFAULT 'user',
            created_at         TEXT,
            enabled            INTEGER DEFAULT 1,
            metadata           TEXT           -- JSON object
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id          TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            agent_type  TEXT,
            agent_id    TEXT,
            status      TEXT DEFAULT 'pending',  -- pending|running|done|failed
            priority    INTEGER DEFAULT 5,
            input       TEXT,           -- JSON
            output      TEXT,           -- JSON
            error       TEXT,
            created_at  TEXT,
            started_at  TEXT,
            ended_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS runs (
            id          TEXT PRIMARY KEY,
            task_id     TEXT,
            agent_id    TEXT,
            agent_name  TEXT,
            task_text   TEXT,
            output      TEXT,
            tool_calls  TEXT,           -- JSON array
            iterations  INTEGER,
            started_at  TEXT,
            ended_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS embeddings (
            id          TEXT PRIMARY KEY,
            collection  TEXT NOT NULL,
            text        TEXT NOT NULL,
            embedding   BLOB NOT NULL,  -- numpy float32 array, tobytes()
            metadata    TEXT,           -- JSON string
            added_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_embeddings_collection
            ON embeddings(collection);

        CREATE TABLE IF NOT EXISTS signals (
            id          TEXT PRIMARY KEY,
            from_agent  TEXT NOT NULL,
            to_agent    TEXT,           -- NULL = broadcast
            task_id     TEXT,
            signal_type TEXT NOT NULL,  -- subtask | result | status
            payload     TEXT NOT NULL,  -- JSON
            created_at  TEXT NOT NULL,
            read_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS mabp_outcomes (
            id                 TEXT PRIMARY KEY,
            task_id            TEXT,
            task_text          TEXT,
            agent_type         TEXT,
            routing_layer      TEXT,
            routing_confidence REAL,
            shadow_events      TEXT,    -- JSON: [{code, iteration, trigger, timestamp}]
            shadow_count       INTEGER DEFAULT 0,
            outcome_score      INTEGER, -- 80 success / 40 error
            had_error          INTEGER DEFAULT 0,
            created_at         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mabp_routing_layer
            ON mabp_outcomes(routing_layer);
        CREATE INDEX IF NOT EXISTS idx_mabp_agent_type
            ON mabp_outcomes(agent_type);
    """)
    conn.commit()
    conn.close()


# ── Registry ─────────────────────────────────────────────────────────────────

class AgentRegistry:
    """
    Manages agent definitions.
    Source of truth: registry/agents.json (human-readable, version-controllable)
    Fast lookups: SQLite agents table (auto-synced on load)
    """

    def __init__(self):
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        init_db()
        self._sync_json_to_db()

    # ── JSON ↔ DB sync ─────────────────────────────────────────────────────

    def _sync_json_to_db(self):
        """Sync agents.json → SQLite (JSON is the source of truth)."""
        if not REGISTRY_PATH.exists():
            return
        agents = json.loads(REGISTRY_PATH.read_text())
        conn   = get_db()
        for agent in agents:
            conn.execute("""
                INSERT OR REPLACE INTO agents
                (id, name, type, model, system_prompt, tools, knowledge_base,
                 behavioral_profile, created_by, created_at, enabled, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent["id"],
                agent["name"],
                agent["type"],
                agent.get("model", "claude-sonnet-4-6"),
                agent.get("system_prompt", ""),
                json.dumps(agent.get("tools", [])),
                agent.get("knowledge_base", ""),
                agent.get("behavioral_profile", "Architect"),
                agent.get("created_by", "user"),
                agent.get("created_at", datetime.now(timezone.utc).date().isoformat()),
                1 if agent.get("enabled", True) else 0,
                json.dumps(agent.get("metadata", {})),
            ))
        conn.commit()
        conn.close()

    def _save_json(self, agents: list):
        REGISTRY_PATH.write_text(json.dumps(agents, indent=2))

    # ── CRUD ───────────────────────────────────────────────────────────────

    def list_agents(self, agent_type: str = None, enabled_only: bool = True) -> list[dict]:
        """List all registered agents, optionally filtered by type."""
        conn  = get_db()
        query = "SELECT * FROM agents"
        params = []
        conditions = []
        if enabled_only:
            conditions.append("enabled = 1")
        if agent_type:
            conditions.append("type = ?")
            params.append(agent_type)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"
        rows  = conn.execute(query, params).fetchall()
        conn.close()
        return [self._row_to_dict(r) for r in rows]

    def get_agent(self, agent_id: str) -> Optional[dict]:
        """Get a single agent by ID."""
        conn = get_db()
        row  = conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        conn.close()
        return self._row_to_dict(row) if row else None

    def get_agent_by_name(self, name: str) -> Optional[dict]:
        conn = get_db()
        row  = conn.execute(
            "SELECT * FROM agents WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        conn.close()
        return self._row_to_dict(row) if row else None

    def register_agent(self, spec: dict) -> dict:
        """
        Register a new agent. Writes to JSON + SQLite.
        Generates an ID if not provided.
        """
        if "id" not in spec:
            spec["id"] = str(uuid.uuid4())
        if "created_at" not in spec:
            spec["created_at"] = datetime.now(timezone.utc).date().isoformat()
        spec.setdefault("enabled", True)
        spec.setdefault("created_by", "user")

        # Update JSON
        agents = self._load_json()
        agents = [a for a in agents if a["id"] != spec["id"]]  # replace if exists
        agents.append(spec)
        self._save_json(agents)

        # Update DB
        conn = get_db()
        conn.execute("""
            INSERT OR REPLACE INTO agents
            (id, name, type, model, system_prompt, tools, knowledge_base,
             behavioral_profile, created_by, created_at, enabled, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            spec["id"],
            spec["name"],
            spec["type"],
            spec.get("model", "claude-sonnet-4-6"),
            spec.get("system_prompt", ""),
            json.dumps(spec.get("tools", [])),
            spec.get("knowledge_base", ""),
            spec.get("behavioral_profile", "Architect"),
            spec.get("created_by", "user"),
            spec["created_at"],
            1 if spec.get("enabled", True) else 0,
            json.dumps(spec.get("metadata", {})),
        ))
        conn.commit()
        conn.close()
        return spec

    def disable_agent(self, agent_id: str) -> bool:
        conn = get_db()
        conn.execute("UPDATE agents SET enabled = 0 WHERE id = ?", (agent_id,))
        conn.commit()
        conn.close()
        # Update JSON too
        agents = self._load_json()
        for a in agents:
            if a["id"] == agent_id:
                a["enabled"] = False
        self._save_json(agents)
        return True

    def _load_json(self) -> list:
        if REGISTRY_PATH.exists():
            return json.loads(REGISTRY_PATH.read_text())
        return []

    def _row_to_dict(self, row) -> dict:
        if row is None:
            return None
        d = dict(row)
        for field in ("tools", "metadata"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    pass
        d["enabled"] = bool(d.get("enabled", 1))
        return d

    def summary(self) -> dict:
        conn   = get_db()
        total  = conn.execute("SELECT COUNT(*) FROM agents WHERE enabled = 1").fetchone()[0]
        by_type = {}
        for row in conn.execute(
            "SELECT type, COUNT(*) as n FROM agents WHERE enabled = 1 GROUP BY type"
        ):
            by_type[row["type"]] = row["n"]
        conn.close()
        return {"total": total, "by_type": by_type}


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: Optional[AgentRegistry] = None

def get_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--list",     action="store_true", help="List all agents")
    parser.add_argument("--summary",  action="store_true", help="Show registry summary")
    parser.add_argument("--type",     type=str, help="Filter by agent type")
    args = parser.parse_args()

    registry = get_registry()

    if args.summary:
        print(json.dumps(registry.summary(), indent=2))
    elif args.list:
        agents = registry.list_agents(agent_type=args.type)
        print(json.dumps(agents, indent=2))
    else:
        print(json.dumps(registry.summary(), indent=2))
