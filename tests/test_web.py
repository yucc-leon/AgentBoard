"""Smoke tests for the web app (no auth, no live tmux needed)."""

from fastapi.testclient import TestClient

from agentboard.config import Config, MachineConfig
from agentboard.web.app import create_app


def _client():
    cfg = Config(machines=[MachineConfig(name="local", type="local")])
    return TestClient(create_app(cfg))


def test_health():
    assert _client().get("/health").json()["status"] == "ok"


def test_dashboard_renders():
    r = _client().get("/")
    assert r.status_code == 200
    assert "Agent Sessions" in r.text


def test_api_sessions_shape():
    r = _client().get("/api/sessions")
    assert r.status_code == 200
    assert "sessions" in r.json()


def test_api_machines():
    r = _client().get("/api/machines")
    assert r.json()["machines"][0]["name"] == "local"


def test_unknown_session_404():
    c = _client()
    assert c.get("/api/sessions/local/does-not-exist/transcript").status_code == 404
    assert c.get("/s/local/does-not-exist").status_code == 404


def test_create_unknown_machine():
    r = _client().post("/api/sessions", json={"machine": "ghost", "cwd": "/tmp"})
    assert r.status_code == 404
