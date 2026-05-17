#!/usr/bin/env python3
"""
Agent Platform — Task Queue
============================
SQLite-backed task queue (local D1 equivalent).
Compatible with the Cloudflare D1 schema used by the dispatcher Worker.

Task lifecycle: pending → running → done | failed
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PLATFORM_DIR = Path(__file__).parent.parent
DB_PATH      = PLATFORM_DIR / "registry" / "agent_platform.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


class TaskQueue:
    """
    SQLite-backed task queue with priority support.
    All datetimes are UTC ISO 8601.
    """

    # ── Write operations ──────────────────────────────────────────────────

    def push_task(
        self,
        description: str,
        agent_type:  str  = None,
        agent_id:    str  = None,
        priority:    int  = 5,
        input_data:  dict = None,
    ) -> str:
        """Enqueue a new task. Returns task_id."""
        task_id = str(uuid.uuid4())
        conn    = get_db()
        conn.execute("""
            INSERT INTO tasks (id, description, agent_type, agent_id, status, priority,
                               input, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
        """, (
            task_id,
            description,
            agent_type,
            agent_id,
            priority,
            json.dumps(input_data or {}),
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
        conn.close()
        return task_id

    def claim_task(self, agent_type: str = None) -> Optional[dict]:
        """
        Atomically claim the highest-priority pending task.
        Optionally filter by agent_type.
        Returns the task dict or None if queue is empty.
        """
        conn = get_db()
        try:
            query  = "SELECT * FROM tasks WHERE status = 'pending'"
            params = []
            if agent_type:
                query  += " AND (agent_type = ? OR agent_type IS NULL)"
                params.append(agent_type)
            query += " ORDER BY priority ASC, created_at ASC LIMIT 1"

            row = conn.execute(query, params).fetchone()
            if not row:
                return None

            task = dict(row)
            conn.execute(
                "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), task["id"]),
            )
            conn.commit()
            task["status"] = "running"
            if task.get("input"):
                try:
                    task["input"] = json.loads(task["input"])
                except Exception:
                    pass
            return task
        finally:
            conn.close()

    def complete_task(self, task_id: str, output: dict) -> bool:
        """Mark a task as done and store its output."""
        conn = get_db()
        conn.execute("""
            UPDATE tasks
            SET status = 'done', output = ?, ended_at = ?
            WHERE id = ?
        """, (json.dumps(output), datetime.now(timezone.utc).isoformat(), task_id))
        conn.commit()
        conn.close()
        return True

    def fail_task(self, task_id: str, error: str) -> bool:
        """Mark a task as failed with an error message."""
        conn = get_db()
        conn.execute("""
            UPDATE tasks
            SET status = 'failed', error = ?, ended_at = ?
            WHERE id = ?
        """, (error, datetime.now(timezone.utc).isoformat(), task_id))
        conn.commit()
        conn.close()
        return True

    # ── Read operations ───────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[dict]:
        conn = get_db()
        row  = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        if not row:
            return None
        task = dict(row)
        for field in ("input", "output"):
            if task.get(field):
                try:
                    task[field] = json.loads(task[field])
                except Exception:
                    pass
        return task

    def list_tasks(
        self,
        status:     str = None,
        agent_type: str = None,
        limit:      int = 50,
    ) -> list[dict]:
        conn   = get_db()
        query  = "SELECT * FROM tasks"
        params = []
        conds  = []
        if status:
            conds.append("status = ?")
            params.append(status)
        if agent_type:
            conds.append("agent_type = ?")
            params.append(agent_type)
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        conn.close()
        result = []
        for row in rows:
            task = dict(row)
            for field in ("input", "output"):
                if task.get(field):
                    try:
                        task[field] = json.loads(task[field])
                    except Exception:
                        pass
            result.append(task)
        return result

    def queue_stats(self) -> dict:
        conn = get_db()
        stats = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) as n FROM tasks GROUP BY status"
        ):
            stats[row["status"]] = row["n"]
        conn.close()
        return stats

    def clear_failed_tasks(self) -> int:
        conn = get_db()
        cur  = conn.execute("DELETE FROM tasks WHERE status = 'failed'")
        conn.commit()
        count = cur.rowcount
        conn.close()
        return count

    def log_mabp_outcome(
        self,
        task_id:            str,
        task_text:          str,
        agent_type:         str,
        routing_layer:      str,
        routing_confidence: float,
        shadow_summary:     dict,
        had_error:          bool = False,
    ) -> str:
        """Log a MABP routing outcome after task resolution."""
        outcome_id    = str(uuid.uuid4())
        shadow_events = shadow_summary.get("events", []) if shadow_summary else []
        shadow_count  = shadow_summary.get("events_detected", 0) if shadow_summary else 0
        outcome_score = 40 if had_error else 80
        conn = get_db()
        conn.execute("""
            INSERT INTO mabp_outcomes
            (id, task_id, task_text, agent_type, routing_layer, routing_confidence,
             shadow_events, shadow_count, outcome_score, had_error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            outcome_id,
            task_id or "",
            (task_text or "")[:200],
            agent_type,
            routing_layer,
            routing_confidence,
            json.dumps(shadow_events),
            shadow_count,
            outcome_score,
            1 if had_error else 0,
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
        conn.close()
        return outcome_id

    def pending_count(self, agent_type: str = None) -> int:
        conn   = get_db()
        query  = "SELECT COUNT(*) FROM tasks WHERE status = 'pending'"
        params = []
        if agent_type:
            query  += " AND (agent_type = ? OR agent_type IS NULL)"
            params.append(agent_type)
        count = conn.execute(query, params).fetchone()[0]
        conn.close()
        return count


# ── Module-level singleton ────────────────────────────────────────────────────

_queue: Optional[TaskQueue] = None

def get_queue() -> TaskQueue:
    global _queue
    if _queue is None:
        _queue = TaskQueue()
    return _queue


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Task Queue CLI")
    parser.add_argument("--stats",  action="store_true", help="Show queue statistics")
    parser.add_argument("--list",   action="store_true", help="List tasks")
    parser.add_argument("--status", type=str, help="Filter by status")
    parser.add_argument("--push",   type=str, help="Push a test task with this description")
    parser.add_argument("--type",   type=str, help="Agent type for push/list")
    args = parser.parse_args()

    # Ensure DB is initialized
    from core.agent_registry import init_db
    init_db()

    q = get_queue()

    if args.push:
        task_id = q.push_task(args.push, agent_type=args.type)
        print(f"Task pushed: {task_id}")
    elif args.stats:
        print(json.dumps(q.queue_stats(), indent=2))
    elif args.list:
        tasks = q.list_tasks(status=args.status, agent_type=args.type)
        print(json.dumps(tasks, indent=2))
    else:
        print(json.dumps(q.queue_stats(), indent=2))
