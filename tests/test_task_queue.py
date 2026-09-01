import sqlite3
from datetime import datetime, timedelta, timezone

from core.task_queue import TaskQueue, get_db, MAX_REAP_ATTEMPTS


def test_push_creates_pending_task(tmp_db):
    q = TaskQueue()
    task_id = q.push_task("do the thing", agent_type="ops")
    task = q.get_task(task_id)
    assert task is not None
    assert task["status"] == "pending"
    assert task["agent_type"] == "ops"


def test_claim_claims_highest_priority_pending(tmp_db):
    q = TaskQueue()
    low_priority_id  = q.push_task("low priority", priority=9)
    high_priority_id = q.push_task("high priority", priority=1)

    claimed = q.claim_task()
    assert claimed["id"] == high_priority_id
    assert claimed["status"] == "running"

    # low-priority task is untouched, still pending
    assert q.get_task(low_priority_id)["status"] == "pending"


def test_claim_returns_none_when_nothing_pending(tmp_db):
    q = TaskQueue()
    assert q.claim_task() is None


def test_complete_task_stores_output_and_resolved_agent(tmp_db):
    q = TaskQueue()
    task_id = q.push_task("auto-routed task")
    q.complete_task(
        task_id,
        {"output": "done!", "tool_calls": 2, "iterations": 1},
        agent_type="python",
        agent_id="agent-123",
    )
    task = q.get_task(task_id)
    assert task["status"] == "done"
    assert task["agent_type"] == "python"
    assert task["output"]["output"] == "done!"


def test_fail_task_transitions_to_failed(tmp_db):
    q = TaskQueue()
    task_id = q.push_task("will fail")
    q.fail_task(task_id, "boom")
    task = q.get_task(task_id)
    assert task["status"] == "failed"
    assert task["error"] == "boom"


def test_list_tasks_filters_by_status(tmp_db):
    q = TaskQueue()
    pending_id = q.push_task("stays pending")
    done_id = q.push_task("will be done")
    q.complete_task(done_id, {"output": "ok"})

    pending_tasks = q.list_tasks(status="pending")
    done_tasks = q.list_tasks(status="done")

    assert {t["id"] for t in pending_tasks} == {pending_id}
    assert {t["id"] for t in done_tasks} == {done_id}


def test_queue_stats_counts_by_status(tmp_db):
    q = TaskQueue()
    q.push_task("a")
    done_id = q.push_task("b")
    q.complete_task(done_id, {"output": "ok"})
    failed_id = q.push_task("c")
    q.fail_task(failed_id, "err")

    stats = q.queue_stats()
    assert stats.get("pending") == 1
    assert stats.get("done") == 1
    assert stats.get("failed") == 1


def test_clear_failed_tasks_only_removes_failed(tmp_db):
    q = TaskQueue()
    pending_id = q.push_task("stays")
    failed_id = q.push_task("goes")
    q.fail_task(failed_id, "err")

    cleared = q.clear_failed_tasks()
    assert cleared == 1
    assert q.get_task(pending_id) is not None
    assert q.get_task(failed_id) is None


# ── Lease / heartbeat / reap ────────────────────────────────────────────────

def _backdate_lease(db_path, task_id: str, when) -> None:
    """
    Directly rewrite lease_expires_at, bypassing the queue API — stands in
    for "time has passed since the orchestrator died mid-task" without
    actually sleeping for the real 10-minute lease window in a test.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE tasks SET lease_expires_at = ? WHERE id = ?",
        (when.isoformat(), task_id),
    )
    conn.commit()
    conn.close()


def test_claim_task_sets_lease_heartbeat_and_attempts(tmp_db):
    q = TaskQueue()
    task_id = q.push_task("do the thing")

    claimed = q.claim_task()

    assert claimed["id"] == task_id
    assert claimed["lease_expires_at"] is not None
    assert claimed["heartbeat_at"] is not None
    assert claimed["attempts"] == 1
    # lease should be set roughly 10 minutes out, not left null/immediate
    lease = datetime.fromisoformat(claimed["lease_expires_at"])
    started = datetime.fromisoformat(claimed["started_at"])
    assert (lease - started) > timedelta(minutes=9)


def test_reap_puts_crashed_task_back_to_pending(tmp_db):
    """
    The core repro from the bug report: claim a task (simulating an
    orchestrator that then crashes — no complete_task/fail_task is ever
    called), let its lease expire, and confirm reap_stale_tasks() finds it
    and requeues it rather than leaving it stuck at 'running' forever.
    """
    q = TaskQueue()
    task_id = q.push_task("will get orphaned by a crash")

    claimed = q.claim_task()
    assert claimed["status"] == "running"

    # --- BEFORE: process "dies" here, nothing ever completes/fails the task ---
    before = q.get_task(task_id)
    assert before["status"] == "running"
    assert before["lease_expires_at"] is not None

    # Simulate the 10-minute lease window having elapsed since the crash.
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    _backdate_lease(tmp_db, task_id, expired)

    reaped_ids = q.reap_stale_tasks()

    # --- AFTER: reaper found it and put it back to pending ---
    after = q.get_task(task_id)
    assert reaped_ids == [task_id]
    assert after["status"] == "pending"
    assert after["lease_expires_at"] is None
    assert after["heartbeat_at"] is None
    assert after["started_at"] is None
    assert after["attempts"] == 1  # attempt count is preserved, not reset

    # and it's claimable again, exactly like a fresh pending task
    reclaimed = q.claim_task()
    assert reclaimed["id"] == task_id
    assert reclaimed["attempts"] == 2


def test_reap_leaves_task_with_unexpired_lease_alone(tmp_db):
    q = TaskQueue()
    task_id = q.push_task("still actively running")
    q.claim_task()  # fresh claim → lease ~10 min in the future

    reaped_ids = q.reap_stale_tasks()

    assert reaped_ids == []
    assert q.get_task(task_id)["status"] == "running"


def test_heartbeat_extends_lease_so_long_task_survives_reap(tmp_db):
    """
    A task that keeps heartbeating past its original 10-minute lease must
    NOT be reaped — only a task that has actually stopped heartbeating
    (crashed process) should be. This is what makes the heartbeat
    mechanically meaningful rather than a diagnostic-only field.
    """
    q = TaskQueue()
    task_id = q.push_task("long-running but healthy")
    q.claim_task()

    # Original lease is already in the past, but the agent is still alive
    # and just heartbeated.
    _backdate_lease(tmp_db, task_id, datetime.now(timezone.utc) - timedelta(seconds=1))
    q.heartbeat_task(task_id)

    reaped_ids = q.reap_stale_tasks()

    assert reaped_ids == []
    assert q.get_task(task_id)["status"] == "running"


def test_reap_respects_max_attempts(tmp_db):
    """
    A task that has already been reaped MAX_REAP_ATTEMPTS times is left at
    'running' rather than requeued forever — surfaces for a human via
    list_tasks(status='running') instead of looping silently.
    """
    q = TaskQueue()
    task_id = q.push_task("keeps crashing every time")

    # Each claim/crash/reap cycle increments attempts by 1. Attempts 1..
    # MAX_REAP_ATTEMPTS-1 are still under the cap and get requeued.
    for _ in range(MAX_REAP_ATTEMPTS - 1):
        claimed = q.claim_task()
        assert claimed["status"] == "running"
        _backdate_lease(tmp_db, task_id, datetime.now(timezone.utc) - timedelta(seconds=1))
        reaped = q.reap_stale_tasks()
        assert reaped == [task_id]  # still under the cap, gets requeued

    # This claim brings attempts to exactly MAX_REAP_ATTEMPTS.
    q.claim_task()
    _backdate_lease(tmp_db, task_id, datetime.now(timezone.utc) - timedelta(seconds=1))
    reaped = q.reap_stale_tasks()

    assert reaped == []
    final = q.get_task(task_id)
    assert final["status"] == "running"  # left alone, not silently requeued forever
    assert final["attempts"] == MAX_REAP_ATTEMPTS


def test_migration_adds_lease_columns_to_preexisting_table(tmp_db):
    """
    A DB created before this feature existed (tasks table with no
    lease_expires_at/heartbeat_at/attempts columns) must be migrated
    transparently on the next get_db() call, not require a manual step.
    """
    # Drop back to the pre-migration shape to simulate an old, live DB.
    conn = sqlite3.connect(str(tmp_db))
    conn.executescript("""
        CREATE TABLE tasks_old_shape (
            id TEXT PRIMARY KEY, description TEXT NOT NULL, agent_type TEXT,
            agent_id TEXT, status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 5,
            input TEXT, output TEXT, error TEXT, created_at TEXT, started_at TEXT,
            ended_at TEXT
        );
        DROP TABLE tasks;
        ALTER TABLE tasks_old_shape RENAME TO tasks;
    """)
    conn.commit()
    conn.close()

    cols_before = {row[1] for row in sqlite3.connect(str(tmp_db)).execute("PRAGMA table_info(tasks)")}
    assert "lease_expires_at" not in cols_before

    conn = get_db()  # triggers migration
    conn.close()

    cols_after = {row[1] for row in sqlite3.connect(str(tmp_db)).execute("PRAGMA table_info(tasks)")}
    assert {"lease_expires_at", "heartbeat_at", "attempts"} <= cols_after
