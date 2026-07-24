from core.task_queue import TaskQueue


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
