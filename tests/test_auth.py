"""Tests for auth: default-deny middleware + token extraction + redaction."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentboard.auth.middleware import AuthMiddleware, _is_exempt
from agentboard.redaction import redact_text


def _app(token="ab_secret"):
    app = FastAPI()
    app.add_middleware(AuthMiddleware, token=token)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/sessions")
    def sessions():
        return {"sessions": []}

    @app.get("/")
    def home():
        return {"home": True}

    return app


def test_exempt_paths():
    assert _is_exempt("/health")
    assert _is_exempt("/static/x.css")
    assert _is_exempt("/login")
    assert not _is_exempt("/api/sessions")
    assert not _is_exempt("/")


def test_api_requires_token():
    c = TestClient(_app())
    assert c.get("/health").status_code == 200          # exempt
    assert c.get("/api/sessions").status_code == 401     # protected, no token
    r = c.get("/api/sessions", headers={"Authorization": "Bearer ab_secret"})
    assert r.status_code == 200


def test_query_param_token_sets_cookie():
    c = TestClient(_app())
    r = c.get("/?token=ab_secret")
    assert r.status_code == 200
    assert "token" in r.headers.get("set-cookie", "")


def test_page_without_token_redirects_to_login():
    c = TestClient(_app())
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_redaction():
    assert "[REDACTED" in redact_text("api_key=sk-abcdefghijklmnopqrstuvwxyz123")
    assert "[REDACTED_PRIVATE_KEY]" in redact_text(
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    )
    assert redact_text("just some normal text") == "just some normal text"
