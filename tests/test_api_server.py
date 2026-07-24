import pytest
from fastapi.testclient import TestClient

import api_server
from api_server import _check_rate_limit


@pytest.fixture
def client():
    return TestClient(api_server.app)


def test_task_status_requires_auth_when_key_configured(tmp_db, client, monkeypatch):
    """
    Regression test: GET /task/{id} previously had zero auth at all — anyone
    with a task_id (UUIDs, but still) could read someone else's task output.
    """
    monkeypatch.setenv("PLATFORM_API_KEY", "correct-key")

    resp = client.get("/task/some-id")
    assert resp.status_code == 401

    resp = client.get("/task/some-id", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401

    resp = client.get("/task/some-id", headers={"Authorization": "Bearer correct-key"})
    # 404 (task doesn't exist), not 401 — proves the key was accepted
    assert resp.status_code == 404


def test_task_status_open_in_dev_mode_without_key_configured(tmp_db, client, monkeypatch):
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    resp = client.get("/task/some-id")
    assert resp.status_code == 404  # not 401 — dev mode, no key required


def test_check_rate_limit_allows_up_to_limit_then_blocks(tmp_db):
    allowed = [_check_rate_limit("test-key", limit=3) for _ in range(5)]
    assert allowed == [True, True, True, False, False]


def test_check_rate_limit_is_per_key(tmp_db):
    for _ in range(3):
        assert _check_rate_limit("key-a", limit=3) is True
    assert _check_rate_limit("key-a", limit=3) is False
    # a different key has its own, independent budget
    assert _check_rate_limit("key-b", limit=3) is True
