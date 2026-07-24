"""
Shared pytest fixtures.

core/task_queue.py and core/agent_registry.py both point at the same
DB_PATH module-level constant (registry/agent_platform.db) and get_db()
reads it fresh on every call — so redirecting both to a tmp file per test
is enough to fully isolate tests from the real, live database. No production
code changes needed for this; it's exactly what DB_PATH being a plain
module attribute (rather than baked into a class at import time) enables.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import core.agent_registry as agent_registry
import core.task_queue as task_queue
import core.eval.feedback_loop as feedback_loop
import core.runtime.loader as runtime_loader


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Point every module-level state path this codebase touches at fresh tmp
    files, then init the schema. Covers the task/agent DB + registry JSON,
    and separately core/runtime/control_state.json — dispatch()'s
    _run_feedback() auto-tunes that file on every real run (even with a
    mocked agent), and it has two independent path constants for the same
    file (core/runtime/loader.py's STATE_PATH and core/eval/feedback_loop.py's
    CONTROL_FILE) that both need redirecting or a test run will silently
    pollute the live, real adaptive-tuning state on disk.
    """
    db_path = tmp_path / "test_agent_platform.db"
    registry_path = tmp_path / "test_agents.json"
    control_state_path = tmp_path / "test_control_state.json"
    control_state_path.write_text(json.dumps({
        "swarm_size": 4,
        "spar_weight": 1.0,
        "context_strictness": 0.5,
        "skill_loader_strength": 0.7,
    }))

    monkeypatch.setattr(agent_registry, "DB_PATH", db_path)
    monkeypatch.setattr(agent_registry, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(task_queue, "DB_PATH", db_path)
    monkeypatch.setattr(feedback_loop, "CONTROL_FILE", control_state_path)
    monkeypatch.setattr(runtime_loader, "STATE_PATH", control_state_path)

    agent_registry.init_db()
    return db_path


@pytest.fixture(autouse=True)
def fake_anthropic_key(monkeypatch):
    """
    Orchestrator() raises without ANTHROPIC_API_KEY set, even though none of
    these tests make a real Anthropic call (agent execution is mocked at the
    make_agent() boundary). Setting a fake key keeps the suite runnable in CI
    without a real secret configured.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")
