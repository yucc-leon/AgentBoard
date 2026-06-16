"""Bearer-token authentication for the web hub.

Security model is **default-deny**: every route requires a valid token *except*
an explicit allow-list (static assets, the login page, health check, PWA files).
This is the opposite of the previous protect-a-few-prefixes scheme, which left
``/api/*`` — the routes that actually run commands — wide open.

A token is accepted from any of:
  - ``Authorization: Bearer <token>`` header
  - ``?token=<token>`` query parameter (needed for WebSocket upgrades)
  - the ``token`` cookie (set automatically after the first authenticated load)

Comparisons use :func:`hmac.compare_digest` to avoid timing leaks.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from agentboard.config import AuthConfig

COOKIE_NAME = "token"

# Paths that never require a token.
EXEMPT_PREFIXES = ("/static", "/login", "/health", "/manifest.json", "/sw.js", "/favicon.ico")


def generate_token() -> str:
    return "ab_" + secrets.token_urlsafe(32)


def load_or_create_token(auth_config: AuthConfig, config_path: str = "") -> str:
    """Return the configured token, generating and persisting one if absent."""
    if auth_config.bearer_token:
        return auth_config.bearer_token
    token = generate_token()
    auth_config.bearer_token = token
    if config_path:
        try:
            _save_token_to_config(config_path, token)
        except Exception:
            pass  # non-critical: token still works for this run
    return token


def _save_token_to_config(config_path: str, token: str) -> None:
    """Write the generated token into the ``auth:`` section of the YAML config."""
    path = os.path.expanduser(config_path)
    content = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            content = f.read()

    if re.search(r"^auth:", content, re.MULTILINE):
        if re.search(r"^\s{2,4}bearer_token:", content, re.MULTILINE):
            content = re.sub(
                r"^(\s{2,4}bearer_token:\s*).*$",
                rf"\g<1>{token}",
                content,
                flags=re.MULTILINE,
            )
        else:
            content = re.sub(
                r"^(auth:.*)$",
                rf"\g<1>\n  bearer_token: {token}",
                content,
                flags=re.MULTILINE,
            )
    else:
        content = content.rstrip() + f"\nauth:\n  enabled: true\n  bearer_token: {token}\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # The config now holds the bearer token (and often an LLM API key) — keep it
    # readable only by the owner, not world-readable under a default umask.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def token_from_request(request: Request) -> str:
    """Extract a presented token from header, query param, or cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    qp = request.query_params.get("token")
    if qp:
        return qp
    return request.cookies.get(COOKIE_NAME, "")


def _is_exempt(path: str) -> bool:
    return any(path == e or path.startswith(e + "/") for e in EXEMPT_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Require a valid bearer token on every non-exempt route."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if _is_exempt(request.url.path):
            return await call_next(request)

        presented = token_from_request(request)
        if presented and hmac.compare_digest(presented, self._token):
            response = await call_next(request)
            # Persist the token as a cookie on the first query-param login so
            # subsequent navigations and WebSocket upgrades carry it.
            if request.query_params.get("token"):
                response.set_cookie(
                    key=COOKIE_NAME,
                    value=self._token,
                    httponly=True,   # not readable by JS → an XSS can't steal the token
                    samesite="lax",
                    max_age=86400 * 30,
                )
            return response

        path = request.url.path
        if path.startswith(("/ws", "/api")) or _wants_json(request):
            return JSONResponse(
                {"error": "unauthorized", "message": "Invalid or missing token"},
                status_code=401,
            )
        return RedirectResponse(url="/login", status_code=302)


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("Accept", "")
