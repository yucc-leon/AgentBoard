"""FastAPI web hub — the single control surface for agent sessions.

One model throughout: a session is a tmux session on a machine, addressed by
``(machine, name)``. Local machines run tmux directly; remote machines are
driven over SSH by :class:`agentboard.core.tmux.Tmux`. There is exactly one
control path — no workline indirection, no second proxy.

Routes (everything under ``/api`` and ``/ws`` is auth-protected by middleware):

  GET    /                                  dashboard
  GET    /s/{machine}/{name}                live session page
  GET    /api/sessions                       list sessions across machines
  POST   /api/sessions                       create a new session
  GET    /api/sessions/{m}/{n}/transcript    parsed chat transcript
  GET    /api/sessions/{m}/{n}/output        raw captured screen
  GET    /api/sessions/{m}/{n}/summary       LLM session card (cached)
  POST   /api/sessions/{m}/{n}/send          type a message + Enter
  POST   /api/sessions/{m}/{n}/key           send raw keys (C-c, Escape, ...)
  DELETE /api/sessions/{m}/{n}               kill the session
  GET    /api/machines                       configured machines
  GET    /api/dirs                           directory picker (local or SSH)
  WS     /ws/session/{m}/{n}                 live screen stream + input
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from agentboard.config import Config, MachineConfig
from agentboard.core.sessions import Session, SessionRegistry
from agentboard.core.transcript import TranscriptState, parse_screen
from agentboard.logging import get_logger

logger = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
_jinja = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)

_SSH_OPTS = ["-o", "ConnectTimeout=8", "-o", "BatchMode=yes"]

# A conversation still being written this recently, but not in a tmux session we
# control, is likely running elsewhere (a VS Code terminal, a bare SSH shell).
_ACTIVE_WINDOW_MS = 300_000


def _active_elsewhere(last_activity_ms: int, live: bool) -> bool:
    if live or not last_activity_ms:
        return False
    return (time.time() * 1000 - last_activity_ms) < _ACTIVE_WINDOW_MS


def _render(template: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_jinja.get_template(template).render(**ctx))


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="Agent Session Workboard", version="0.3.0")
    registry = SessionRegistry(config.machines)

    # ------------------------------------------------------------------
    # LLM summary feature state
    #   - available: an LLM API key is configured (else the feature is dead).
    #   - enabled: config.summary.enabled is the default; the dashboard ✨ toggle
    #     overrides it at runtime, persisted to data_dir so the YAML stays clean.
    # ------------------------------------------------------------------
    from agentboard.intelligence.llm import LLMClient

    summary_available = LLMClient(config.llm).available
    _ui_state_path = Path(config.workspace.data_dir).expanduser() / "ui_state.json"

    def _read_ui_state() -> dict:
        try:
            import json as _j
            return _j.loads(_ui_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _summary_enabled() -> bool:
        if not summary_available:
            return False
        override = _read_ui_state().get("summary_enabled")
        return bool(override) if isinstance(override, bool) else config.summary.enabled

    def _set_summary_enabled(value: bool) -> None:
        import json as _j

        config.summary.enabled = value
        state = _read_ui_state()
        state["summary_enabled"] = value
        try:
            _ui_state_path.parent.mkdir(parents=True, exist_ok=True)
            _ui_state_path.write_text(_j.dumps(state), encoding="utf-8")
        except OSError:
            pass

    auth_required = config.auth.enabled and config.remote.enabled
    from agentboard.auth.middleware import AuthMiddleware, load_or_create_token

    token = load_or_create_token(config.auth)
    if auth_required:
        app.add_middleware(AuthMiddleware, token=token)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ------------------------------------------------------------------
    # Helpers bound to this app's config
    # ------------------------------------------------------------------

    def _machine(name: str) -> MachineConfig | None:
        return next((m for m in config.machines if m.name == name), None)

    def _require_session(machine: str, name: str, *, refresh: bool = False) -> Session | None:
        return registry.get(machine, name, refresh=refresh)

    def _transcript(session: Session) -> TranscriptState:
        """Clean chat for this pane, parsed from the JSONL it's actually writing.

        For a local agent we map the pane to its own transcript precisely via
        ``lsof`` on the pane's process tree — so this is the real per-pane chat,
        not a guess-by-cwd (which could show a neighbour's conversation) and not
        a wasteful constant re-render of the raw TUI. Remote panes, or anything
        we can't map, fall back to parsing the captured screen.
        """
        from agentboard.core.sessions import jsonl_for_pid
        from agentboard.core.transcript import parse_jsonl_file

        if session.machine_type == "local" and session.is_agent and session.pid:
            path = jsonl_for_pid(session.pid)
            if path:
                state = parse_jsonl_file(path, session.cli)
                if state.messages:
                    return state
        tmux = registry.tmux_for(session.machine)
        text = tmux.capture(session.name, lines=400) if tmux else ""
        return parse_screen(text)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "version": "0.3.0"})

    @app.get("/manifest.json")
    async def manifest():
        return JSONResponse({
            "name": "Agent Session Workboard",
            "short_name": "AgentBoard",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0d1117",
            "theme_color": "#0d1117",
            "icons": [],
        })

    @app.get("/sw.js")
    async def service_worker():
        return PlainTextResponse("// no-op service worker\n",
                                 media_type="application/javascript")

    @app.get("/favicon.ico")
    async def favicon():
        return PlainTextResponse("", status_code=204)

    @app.get("/login", response_class=HTMLResponse)
    async def login(request: Request):
        return _render("login.html", request=request)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, refresh: bool = False):
        from agentboard.intelligence.summary import cached_card, cached_title

        # Group everything by project (the working directory's leaf name), so the
        # dashboard is a two-level menu: project → its live sessions + conversations.
        groups: dict[str, dict] = {}

        def _group(project: str, machine: str, cwd: str) -> dict:
            key = f"{machine}:{project}" if machine != "local" else project
            if key not in groups:
                groups[key] = {"project": project or "(no project)", "machine": machine,
                               "cwd": cwd, "live": [], "convs": [], "recent": 0}
            elif cwd and not groups[key]["cwd"]:
                groups[key]["cwd"] = cwd
            return groups[key]

        for s in registry.list(refresh=refresh):
            card = cached_card(config, s.key)
            project = _proj(s.cwd)
            g = _group(project, s.machine, s.cwd)
            g["live"].append({
                "s": s,
                "title": card.title if (card and card.title) else "",
                "open_items": len(card.open_items) if card else 0,
                "next_action": card.next_action if card else "",
            })

        for c in registry.conversations(refresh=refresh):
            card = cached_card(config, c.key)
            # Title precedence: full card > cheap quick-title > first message.
            title = (card.title if (card and card.title) else None) \
                or cached_title(config, c.key) or c.title
            g = _group(c.project, c.machine, c.cwd)
            g["convs"].append({
                "c": c,
                "title": title,
                "open_items": len(card.open_items) if card else 0,
                "active": _active_elsewhere(c.last_activity_ms, bool(c.live_tmux)),
            })
            g["recent"] = max(g["recent"], c.last_activity_ms)

        # Projects with anything live float up; otherwise by most recent activity.
        ordered = sorted(
            groups.values(),
            key=lambda g: (len(g["live"]) > 0, g["recent"]),
            reverse=True,
        )
        machines = [{"name": m.name, "type": m.type} for m in config.machines]
        return _render("dashboard.html", request=request,
                       groups=ordered, machines=machines,
                       summary_enabled=_summary_enabled(),
                       summary_available=summary_available)

    @app.get("/s/{machine}/{name}", response_class=HTMLResponse)
    async def session_page(request: Request, machine: str, name: str):
        s = _require_session(machine, name)
        if not s:
            return HTMLResponse("<h1>Session not found</h1>", status_code=404)
        from agentboard.intelligence.summary import cached_card

        card = cached_card(config, s.key)
        return _render(
            "session_live.html",
            request=request,
            session=s.as_dict(),
            card=card.as_dict() if card else None,
            voice_enabled=config.voice.enabled if config.voice else False,
            summary_enabled=_summary_enabled(),
        )

    # ------------------------------------------------------------------
    # Conversations (JSONL logs) — read / summarize / resume
    # ------------------------------------------------------------------

    def _conv_live_name(conv) -> str | None:
        """Current live tmux session for a conversation, if its agent is running."""
        for s in registry.list():
            if (s.machine == conv.machine and s.is_agent and s.cli == conv.cli
                    and os.path.normpath(s.cwd) == os.path.normpath(conv.cwd)):
                return s.name
        return None

    @app.get("/c/{machine}/{cli}/{session_id}", response_class=HTMLResponse)
    async def conversation_page(request: Request, machine: str, cli: str, session_id: str):
        from agentboard.intelligence.summary import cached_card

        conv = registry.find_conversation(machine, cli, session_id)
        if not conv:
            return HTMLResponse("<h1>Conversation not found</h1>", status_code=404)
        card = cached_card(config, conv.key)
        d = conv.as_dict()
        d["live_tmux"] = _conv_live_name(conv)
        return _render(
            "conversation.html",
            request=request,
            conv=d,
            card=card.as_dict() if card else None,
            summary_enabled=_summary_enabled(),
            active_elsewhere=_active_elsewhere(conv.last_activity_ms, bool(d["live_tmux"])),
        )

    @app.get("/api/conversations")
    async def api_conversations():
        return {"conversations": [c.as_dict() for c in registry.conversations(refresh=True)]}

    # ------------------------------------------------------------------
    # Summary feature controls
    # ------------------------------------------------------------------

    @app.get("/api/summary/state")
    async def summary_state():
        return {"enabled": _summary_enabled(), "available": summary_available}

    @app.post("/api/summary/state")
    async def set_summary_state(payload: dict):
        if not summary_available:
            return JSONResponse(
                {"error": "no LLM configured", "available": False}, status_code=400
            )
        _set_summary_enabled(bool(payload.get("enabled")))
        return {"enabled": _summary_enabled(), "available": summary_available}

    @app.post("/api/summarize-recent")
    async def summarize_recent(count: int | None = None):
        """Give recent conversations a cheap LLM title (the list-facing label).

        Titles are derived from the opening message — one tiny LLM call each, no
        transcript read — so a whole list can be labelled affordably. The heavy
        card (recap + open items) is still generated lazily when a conversation
        is opened, and its title supersedes the quick one.
        """
        if not _summary_enabled():
            return JSONResponse({"error": "summaries disabled"}, status_code=400)
        from agentboard.intelligence.summary import (
            cached_card,
            cached_title,
            quick_title,
        )

        n = count or max(config.summary.recent_count, 40)
        convs = registry.conversations()[:n]
        todo = [
            c for c in convs
            if not (cached_title(config, c.key) or cached_card(config, c.key))
        ]

        # Each title needs a transcript read + an LLM call (slow on reasoning
        # models), so run a few concurrently rather than one-at-a-time.
        sem = asyncio.Semaphore(5)

        async def _title_one(c) -> tuple[str, str] | None:
            async with sem:
                try:
                    state = await asyncio.to_thread(registry.conversation_transcript, c)
                    title = await quick_title(config, c.key, state)
                    return (c.key, title) if title else None
                except Exception:
                    logger.debug("quick_title failed for %s", c.key, exc_info=True)
                    return None

        results = await asyncio.gather(*[_title_one(c) for c in todo])
        titles = {k: v for r in results if r for k, v in [r]}
        return {"ok": True, "titled": len(titles), "scanned": len(convs), "titles": titles}

    @app.get("/api/conversations/{machine}/{cli}/{session_id}/transcript")
    async def api_conv_transcript(machine: str, cli: str, session_id: str):
        conv = registry.find_conversation(machine, cli, session_id)
        if not conv:
            return JSONResponse({"error": "not found"}, status_code=404)
        return registry.conversation_transcript(conv).as_dict()

    @app.get("/api/conversations/{machine}/{cli}/{session_id}/summary")
    async def api_conv_summary(machine: str, cli: str, session_id: str, force: bool = False):
        if not _summary_enabled():
            return JSONResponse({"error": "summaries disabled"}, status_code=503)
        from agentboard.intelligence.summary import summarize_session

        conv = registry.find_conversation(machine, cli, session_id)
        if not conv:
            return JSONResponse({"error": "not found"}, status_code=404)
        state = registry.conversation_transcript(conv)
        card = await summarize_session(config, conv.key, state, force=force)
        if not card:
            return JSONResponse(
                {"error": "no_summary", "detail": "No LLM configured or transcript too thin"},
                status_code=503,
            )
        return card.as_dict()

    @app.post("/api/conversations/{machine}/{cli}/{session_id}/resume")
    async def api_conv_resume(machine: str, cli: str, session_id: str):
        conv = registry.find_conversation(machine, cli, session_id)
        if not conv:
            return JSONResponse({"error": "not found"}, status_code=404)

        # Idempotent: if this conversation is already running in a tmux session,
        # just hand back that one instead of spawning another '-resume' clone.
        existing = _conv_live_name(conv)
        if existing:
            return {"ok": True, "machine": machine, "name": existing, "reused": True}

        tmux = registry.tmux_for(machine)
        if not tmux:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        if conv.cli == "claude":
            command = f"claude --resume {session_id}"
        else:
            command = f"codex resume {session_id}"
        # Clean, stable name derived from the project + short id (no '-resume'
        # suffix piling up). Reused on repeat because has_session matches it.
        name = _safe_name(f"{conv.project or conv.cli}-{session_id[:6]}")
        try:
            if not tmux.has_session(name):
                tmux.new_session(name, conv.cwd or ".", command)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        registry.list(refresh=True)
        return {"ok": True, "machine": machine, "name": name}

    # ------------------------------------------------------------------
    # Session API
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    async def api_list(refresh: bool = False):
        return {"sessions": [s.as_dict() for s in registry.list(refresh=refresh)]}

    @app.get("/api/machines")
    async def api_machines():
        return {"machines": [{"name": m.name, "type": m.type, "host": m.host}
                             for m in config.machines]}

    @app.post("/api/sessions")
    async def api_create(payload: dict):
        machine = payload.get("machine", "local")
        name = _safe_name(payload.get("name", "agent"))
        cwd = payload.get("cwd") or "."
        command = payload.get("command") or "codex"
        tmux = registry.tmux_for(machine)
        if not tmux:
            return JSONResponse({"error": f"unknown machine: {machine}"}, status_code=404)
        if tmux.has_session(name):
            return JSONResponse({"error": f"session '{name}' already exists"}, status_code=409)
        try:
            tmux.new_session(name, cwd, command)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        registry.list(refresh=True)  # warm the cache so the new session shows up
        return {"ok": True, "machine": machine, "name": name}

    @app.get("/api/sessions/{machine}/{name}/transcript")
    async def api_transcript(machine: str, name: str):
        s = _require_session(machine, name)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        return _transcript(s).as_dict()

    @app.get("/api/sessions/{machine}/{name}/output")
    async def api_output(machine: str, name: str, lines: int = 200):
        tmux = registry.tmux_for(machine)
        if not tmux:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        return {"output": tmux.capture(name, lines=lines)}

    @app.post("/api/sessions/{machine}/{name}/send")
    async def api_send(machine: str, name: str, payload: dict):
        text = (payload.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        tmux = registry.tmux_for(machine)
        if not tmux:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        try:
            await asyncio.to_thread(tmux.send, name, text, enter=payload.get("enter", True))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"ok": True}

    @app.post("/api/sessions/{machine}/{name}/key")
    async def api_key(machine: str, name: str, payload: dict):
        keys = payload.get("keys") or []
        if not isinstance(keys, list) or not keys:
            return JSONResponse({"error": "keys required"}, status_code=400)
        tmux = registry.tmux_for(machine)
        if not tmux:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        try:
            await asyncio.to_thread(tmux.send_keys, name, [str(k) for k in keys])
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"ok": True}

    @app.delete("/api/sessions/{machine}/{name}")
    async def api_kill(machine: str, name: str):
        tmux = registry.tmux_for(machine)
        if not tmux:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        try:
            tmux.kill_session(name)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        registry.list(refresh=True)
        return {"ok": True}

    @app.get("/api/sessions/{machine}/{name}/summary")
    async def api_summary(machine: str, name: str, force: bool = False):
        if not _summary_enabled():
            return JSONResponse({"error": "summaries disabled"}, status_code=503)
        s = _require_session(machine, name)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        from agentboard.intelligence.summary import summarize_session

        state = _transcript(s)
        card = await summarize_session(config, s.key, state, force=force)
        if not card:
            return JSONResponse(
                {"error": "no_summary", "detail": "No LLM configured or transcript too thin"},
                status_code=503,
            )
        return card.as_dict()

    @app.get("/api/dirs")
    async def api_dirs(machine: str = "local", path: str = "~"):
        mc = _machine(machine)
        if not mc:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        host = mc.host if mc.type == "ssh" else None
        try:
            entries, resolved = await asyncio.to_thread(_list_dirs, host, path)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"path": resolved, "entries": entries}

    # ------------------------------------------------------------------
    # Voice (optional server-side TTS)
    # ------------------------------------------------------------------

    @app.post("/api/voice/tts")
    async def voice_tts(payload: dict):
        if not (config.voice and config.voice.enabled):
            return JSONResponse({"error": "voice disabled"}, status_code=400)
        text = (payload.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "no text"}, status_code=400)
        from agentboard.voice.pipeline import VoicePipeline

        result = await VoicePipeline(config.voice).synthesize(text, language=config.voice.language)
        if not result:
            return JSONResponse({"error": "tts failed"}, status_code=500)
        return {"ok": True, "audio_base64": result.audio_base64,
                "format": result.format, "provider": result.provider}

    # ------------------------------------------------------------------
    # WebSocket — live screen stream + input
    # ------------------------------------------------------------------

    @app.websocket("/ws/session/{machine}/{name}")
    async def ws_session(ws: WebSocket, machine: str, name: str):
        if auth_required:
            from agentboard.auth.middleware import token_from_request

            if token_from_request(ws) != token:
                await ws.close(code=4001)
                return
        await ws.accept()

        tmux = registry.tmux_for(machine)
        if not tmux:
            await ws.send_json({"type": "error", "content": "unknown machine"})
            await ws.close()
            return

        async def pump_input():
            while True:
                data = await ws.receive_json()
                mtype = data.get("type")
                if mtype == "send":
                    text = (data.get("content") or "").strip()
                    if text:
                        await asyncio.to_thread(tmux.send, name, text, enter=True)
                elif mtype == "key":
                    keys = [str(k) for k in (data.get("keys") or [])]
                    if keys:
                        await asyncio.to_thread(tmux.send_keys, name, keys)

        async def pump_output():
            last = ""
            while True:
                current = await asyncio.to_thread(tmux.capture, name, 120)
                if current != last:
                    await ws.send_json({"type": "screen", "content": current})
                    last = current
                await asyncio.sleep(0.6)

        try:
            await asyncio.gather(pump_input(), pump_output())
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("ws_session closed for %s/%s", machine, name, exc_info=True)

    return app


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _proj(cwd: str) -> str:
    return Path(cwd).name if cwd else ""


def _safe_name(raw: str) -> str:
    import re

    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", raw or "agent")[:40]
    safe = re.sub(r"-{2,}", "-", safe).strip("-")
    return safe or "agent"


def _list_dirs(host: str | None, path: str) -> tuple[list[dict], str]:
    """List sub-directories of ``path`` on a machine (local or over SSH)."""
    if host is None:
        base = Path(os.path.expanduser(path)).resolve()
        if not base.is_dir():
            base = base.parent
        entries = [
            {"name": c.name, "path": str(c)}
            for c in sorted(base.iterdir())
            if c.is_dir() and not c.name.startswith(".")
        ]
        return entries, str(base)

    # Remote: list directories via SSH. ``-d */`` keeps it to sub-dirs only.
    quoted = shlex.quote(path)
    cmd = f"cd {quoted} 2>/dev/null && pwd && ls -1ap | grep '/$'"
    proc = subprocess.run(
        ["ssh", *_SSH_OPTS, host, cmd], capture_output=True, text=True, timeout=20
    )
    out_lines = proc.stdout.splitlines()
    if not out_lines:
        return [], path
    resolved = out_lines[0]
    entries = []
    for line in out_lines[1:]:
        nm = line.rstrip("/")
        if nm in ("", ".", "..") or nm.startswith("."):
            continue
        entries.append({"name": nm, "path": f"{resolved.rstrip('/')}/{nm}"})
    return entries, resolved
