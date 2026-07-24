import core.orchestrator as orchestrator_module
from core.orchestrator import Orchestrator


class _FakeAgent:
    def __init__(self, output="fake output", agent_id="fake-agent-id"):
        self.agent_id = agent_id
        self._output = output

    def run(self, task, context=None):
        return {"output": self._output, "tool_calls": [], "iterations": 1}


def test_dispatch_success_completes_the_right_task(tmp_db, monkeypatch):
    """Explicit agent_type + skip_spar, mocked agent execution — no real API calls."""
    monkeypatch.setattr(orchestrator_module, "make_agent", lambda agent_type, provider=None: _FakeAgent())

    orch = Orchestrator()
    task_id = orch.queue.push_task("do a thing", agent_type="ops")

    result = orch.dispatch("do a thing", agent_type="ops", task_id=task_id, skip_spar=True)

    assert result["output"] == "fake output"
    task = orch.queue.get_task(task_id)
    assert task["status"] == "done"
    assert task["agent_type"] == "ops"


def test_dispatch_by_task_id_never_touches_other_pending_tasks(tmp_db, monkeypatch):
    """
    Regression test for the mcp-server race: dispatching a specific task_id
    must only ever affect that task, even if other tasks are pending —
    the bug this guards against was calling claim_task() (claims globally
    by priority/age) instead of operating on the exact task_id passed in.
    """
    monkeypatch.setattr(orchestrator_module, "make_agent", lambda agent_type, provider=None: _FakeAgent())

    orch = Orchestrator()
    other_task_id = orch.queue.push_task("someone else's task", priority=1)  # higher priority
    my_task_id    = orch.queue.push_task("my task", priority=9)

    orch.dispatch("my task", agent_type="ops", task_id=my_task_id, skip_spar=True)

    assert orch.queue.get_task(my_task_id)["status"] == "done"
    # The other, higher-priority pending task must be completely untouched
    assert orch.queue.get_task(other_task_id)["status"] == "pending"


def test_dispatch_unknown_agent_type_fails_the_task_not_raises(tmp_db):
    orch = Orchestrator()
    task_id = orch.queue.push_task("bad task", agent_type="not_a_real_type")

    result = orch.dispatch("bad task", agent_type="not_a_real_type", task_id=task_id, skip_spar=True)

    assert "error" in result
    task = orch.queue.get_task(task_id)
    assert task["status"] == "failed"


def test_spar_block_transitions_task_to_terminal_state(tmp_db, monkeypatch):
    """
    Regression test for this session's SPAR-stuck-forever bug: previously,
    when the SPAR gate blocked a task, dispatch() returned without ever
    calling complete_task()/fail_task(), so a queued task sat at
    pending/running forever. It must now reach a terminal state ("done",
    since a SPAR stop is an intentional outcome, not an error).
    """
    monkeypatch.setattr(Orchestrator, "complexity_score", lambda self, task: 999)

    class _FakeSPARDebater:
        def __init__(self, orchestrator=None):
            pass

        def run(self, task):
            return {
                "proceed": False,
                "gaps": ["missing requirement"],
                "recommendation": "do not proceed",
            }

    monkeypatch.setattr("core.spar.SPARDebater", _FakeSPARDebater)

    orch = Orchestrator()
    task_id = orch.queue.push_task("a risky task")

    result = orch.dispatch("a risky task", agent_type="ops", task_id=task_id)

    assert result["agent_type"] == "spar"
    task = orch.queue.get_task(task_id)
    assert task["status"] != "pending"
    assert task["status"] != "running"
    assert task["status"] == "done"
