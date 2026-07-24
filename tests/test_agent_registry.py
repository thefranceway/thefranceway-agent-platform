import json

from core.agent_registry import AgentRegistry


def _spec(name="Test Agent", agent_type="ops"):
    return {
        "name": name,
        "type": agent_type,
        "model": "claude-sonnet-4-6",
        "system_prompt": "You are a test agent.",
        "tools": ["bash"],
        "behavioral_profile": "Substrate",
    }


def test_register_agent_writes_json_and_db(tmp_db):
    registry = AgentRegistry()
    result = registry.register_agent(_spec())

    assert result["id"]
    from_db = registry.get_agent(result["id"])
    assert from_db is not None
    assert from_db["name"] == "Test Agent"

    from core.agent_registry import REGISTRY_PATH
    on_disk = json.loads(REGISTRY_PATH.read_text())
    assert any(a["id"] == result["id"] for a in on_disk)


def test_disable_agent_is_soft(tmp_db):
    registry = AgentRegistry()
    result = registry.register_agent(_spec())
    agent_id = result["id"]

    registry.disable_agent(agent_id)

    still_there = registry.get_agent(agent_id)
    assert still_there is not None
    assert still_there["enabled"] is False


def test_delete_agent_by_id_removes_from_db_and_json(tmp_db):
    registry = AgentRegistry()
    result = registry.register_agent(_spec())
    agent_id = result["id"]

    deleted = registry.delete_agent(agent_id=agent_id)
    assert deleted is True
    assert registry.get_agent(agent_id) is None

    from core.agent_registry import REGISTRY_PATH
    on_disk = json.loads(REGISTRY_PATH.read_text())
    assert not any(a["id"] == agent_id for a in on_disk)


def test_delete_agent_by_name(tmp_db):
    registry = AgentRegistry()
    registry.register_agent(_spec(name="Named Agent"))

    deleted = registry.delete_agent(name="Named Agent")
    assert deleted is True
    assert registry.get_agent_by_name("Named Agent") is None


def test_delete_agent_returns_false_for_unknown(tmp_db):
    registry = AgentRegistry()
    assert registry.delete_agent(agent_id="does-not-exist") is False
    assert registry.delete_agent(name="Nonexistent Agent") is False


def test_delete_agent_requires_id_or_name(tmp_db):
    registry = AgentRegistry()
    try:
        registry.delete_agent()
        assert False, "expected ValueError"
    except ValueError:
        pass
