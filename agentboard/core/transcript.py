"""Transcript parsing for Codex and Claude sessions.

Two sources, in order of fidelity:

  1. CLI-native JSONL files (``~/.codex/sessions`` / ``~/.claude/projects``).
     These are authoritative — structured user/agent turns and token usage.
     Used for local sessions, located by matching the session's *cwd*.
  2. Plain-text ``capture-pane`` output. The universal fallback that also works
     over SSH for remote sessions, where we can't easily reach the JSONL.

Parsing logic adapted from principles in botmux (MIT) and StarAgent.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agentboard.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranscriptMessage:
    """A single turn in a conversation — user message or agent reply."""

    role: str  # "user" | "agent"
    text: str
    timestamp_ms: int = 0
    source_id: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "text": self.text,
            "time": self.timestamp_ms,
            "id": self.source_id,
        }


@dataclass(frozen=True)
class TokenUsage:
    """Token usage tracking for a session."""

    source: str = ""
    model: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    context_window: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "context_window": self.context_window,
        }


@dataclass(frozen=True)
class TranscriptState:
    """Full snapshot of a session's transcript."""

    reply: str = ""
    working: bool = False
    messages: tuple[TranscriptMessage, ...] = ()
    token_usage: TokenUsage | None = None
    source: str = ""  # "codex" | "claude" | "screen"

    def as_dict(self) -> dict[str, object]:
        return {
            "reply": self.reply,
            "working": self.working,
            "messages": [m.as_dict() for m in self.messages],
            "token_usage": self.token_usage.as_dict() if self.token_usage else None,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# JSONL discovery by cwd (more reliable than PID matching)
# ---------------------------------------------------------------------------

# How many of the most-recent JSONL files to inspect when matching a cwd.
_SCAN_LIMIT = 60


def _read_jsonl_events(fpath: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with open(fpath, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.debug("Cannot read JSONL %s: %s", fpath, e)
    return events


def _jsonl_candidates(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files = [p for p in root.rglob("*.jsonl")]
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return files[:_SCAN_LIMIT]


def _norm(path: str) -> str:
    return os.path.normpath(os.path.expanduser(path)).rstrip("/")


def find_codex_jsonl_for_cwd(cwd: str, codex_home: str = "~/.codex") -> Path | None:
    """Locate the most recent Codex rollout whose session cwd matches ``cwd``."""
    home = Path(os.path.expanduser(codex_home))
    target = _norm(cwd)
    best: Path | None = None
    for fpath in _jsonl_candidates(home / "sessions") + _jsonl_candidates(home / "rollouts"):
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                head = f.readline()
            meta = json.loads(head)
        except (OSError, json.JSONDecodeError):
            continue
        payload = meta.get("payload", meta)
        file_cwd = ""
        if isinstance(payload, dict):
            file_cwd = payload.get("cwd", "") or meta.get("cwd", "")
        if file_cwd and _norm(file_cwd) == target:
            return fpath  # candidates are mtime-sorted → first match is newest
        if best is None:
            best = fpath
    return None


def find_claude_jsonl_for_cwd(cwd: str, claude_home: str = "~/.claude") -> Path | None:
    """Locate the most recent Claude transcript whose session cwd matches ``cwd``."""
    projects = Path(os.path.expanduser(claude_home)) / "projects"
    target = _norm(cwd)
    for fpath in _jsonl_candidates(projects):
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                head = f.readline()
            obj = json.loads(head)
        except (OSError, json.JSONDecodeError):
            continue
        file_cwd = obj.get("cwd", "")
        if file_cwd and _norm(file_cwd) == target:
            return fpath
    return None


# ---------------------------------------------------------------------------
# High-level entry points
# ---------------------------------------------------------------------------


def local_transcript_for(
    cwd: str,
    cli: str,
    *,
    codex_home: str = "~/.codex",
    claude_home: str = "~/.claude",
) -> TranscriptState | None:
    """Parse the JSONL transcript of a local session located by its cwd."""
    if cli == "codex":
        fpath = find_codex_jsonl_for_cwd(cwd, codex_home)
        if fpath:
            return _parse_codex_events(_read_jsonl_events(fpath))
    elif cli == "claude":
        fpath = find_claude_jsonl_for_cwd(cwd, claude_home)
        if fpath:
            return _parse_claude_events(_read_jsonl_events(fpath))
    return None


def parse_jsonl_file(path: str | Path, cli: str) -> TranscriptState:
    """Parse a full conversation from its JSONL file path."""
    events = _read_jsonl_events(Path(path))
    return _parse_events(events, cli)


def parse_jsonl_text(text: str, cli: str) -> TranscriptState:
    """Parse a conversation from raw JSONL text (e.g. fetched over SSH)."""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return _parse_events(events, cli)


def _parse_events(events: list[dict[str, Any]], cli: str) -> TranscriptState:
    if cli == "codex":
        return _parse_codex_events(events)
    if cli == "claude":
        return _parse_claude_events(events)
    return TranscriptState(source=cli)


def parse_screen(text: str) -> TranscriptState:
    """Fallback: turn raw ``capture-pane`` text into a minimal transcript state."""
    ansi_re = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
    clean = ansi_re.sub("", text)
    lines = [ln.rstrip() for ln in clean.splitlines() if ln.strip()]
    reply = "\n".join(lines[-40:]).strip()
    working = bool(re.search(r"\b(?:Working|Thinking|Esc to interrupt)\b", clean, re.I))
    return TranscriptState(reply=reply, working=working, source="screen")


# ---------------------------------------------------------------------------
# Codex JSONL parsing
# ---------------------------------------------------------------------------


# Pseudo-user lines the CLIs inject that aren't things the user actually said.
_NOISE_USER = re.compile(
    r"^\s*\[(request interrupted|tool use|no response requested|"
    r"the user (?:doesn't|did not))",
    re.IGNORECASE,
)


def _refine_messages(
    messages: list[TranscriptMessage],
) -> list[TranscriptMessage]:
    """Make the message list read the way a human saw the conversation.

    Two real-world artifacts handled:
      - injected pseudo-user markers ("[Request interrupted by user]", ...) are
        dropped — the user never typed them.
      - retract / keep-typing / resend: when consecutive user turns (no agent
        reply between them) are exact duplicates or one is a prefix of the next
        (the user kept appending before sending), only the final version is
        kept — matching what the CLI itself shows.
    """
    cleaned = [
        m for m in messages
        if not (m.role == "user" and _NOISE_USER.match(m.text or ""))
    ]
    out: list[TranscriptMessage] = []
    for m in cleaned:
        if out and out[-1].role == m.role:
            a, b = out[-1].text.strip(), m.text.strip()
            if a == b:                       # exact duplicate → keep the latest
                out[-1] = m
                continue
            if m.role == "user":
                if b.startswith(a):          # user kept typing / resent longer
                    out[-1] = m
                    continue
                if a.startswith(b):          # later turn is a shorter prefix
                    continue
        out.append(m)
    return out


def _parse_codex_events(events: list[dict[str, Any]]) -> TranscriptState:
    if not events:
        return TranscriptState(source="codex")
    messages = _refine_messages(_codex_messages(events))
    usage = _codex_token_usage(events)
    reply = messages[-1].text if messages and messages[-1].role == "agent" else ""
    working = bool(messages) and messages[-1].role == "user"
    return TranscriptState(
        reply=reply,
        working=working,
        messages=tuple(messages),
        token_usage=usage,
        source="codex",
    )


def _codex_messages(events: list[dict[str, Any]]) -> list[TranscriptMessage]:
    messages: list[TranscriptMessage] = []
    for evt in events:
        kind = evt.get("type", "")
        payload = evt.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        ts = _ts_ms(evt)

        if kind == "response_item" and isinstance(payload, dict):
            item_type = payload.get("type", "")
            role = payload.get("role", "")
            if item_type == "message":
                text = _join_blocks(payload.get("content", []))
                if text and role in ("user", "assistant", "developer"):
                    messages.append(
                        TranscriptMessage(
                            role="user" if role == "user" else "agent",
                            text=text,
                            timestamp_ms=ts,
                            source_id=str(evt.get("id", "")),
                        )
                    )
            elif item_type == "tool_call":
                name = payload.get("name", "tool")
                messages.append(
                    TranscriptMessage(role="agent", text=f"🛠 {name}", timestamp_ms=ts)
                )
        elif kind == "event_msg" and isinstance(payload, dict):
            ptype = payload.get("type", "")
            role = payload.get("role", "")
            # Newer/simple formats put the message itself on an event_msg with a
            # role and content blocks; older ones use a "user_command" text field.
            if ptype == "user_command":
                text = str(payload.get("text", ""))
                if text:
                    messages.append(
                        TranscriptMessage(role="user", text=text, timestamp_ms=ts)
                    )
            elif role in ("user", "assistant", "developer"):
                text = _join_blocks(payload.get("content", payload.get("text", "")))
                if text:
                    messages.append(
                        TranscriptMessage(
                            role="user" if role == "user" else "agent",
                            text=text,
                            timestamp_ms=ts,
                        )
                    )
    return messages


def _codex_token_usage(events: list[dict[str, Any]]) -> TokenUsage | None:
    for evt in reversed(events):
        payload = evt.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict) or "total_token_usage" not in payload:
            continue
        info = payload.get("info", payload)
        total = info.get("total_token_usage", {}) if isinstance(info, dict) else {}
        if not isinstance(total, dict):
            continue
        model = ""
        for e2 in reversed(events):
            if e2.get("type") == "session_meta":
                p2 = e2.get("payload", {})
                if isinstance(p2, dict):
                    model = str(p2.get("model", ""))
                break
        return TokenUsage(
            source="codex",
            model=model,
            input_tokens=int(total.get("input_tokens", 0)),
            cached_input_tokens=int(total.get("cached_input_tokens", 0)),
            output_tokens=int(total.get("output_tokens", 0)),
            total_tokens=int(total.get("total_tokens", 0)),
            context_window=int(info.get("model_context_window", 0))
            if isinstance(info, dict)
            else 0,
        )
    return None


# ---------------------------------------------------------------------------
# Claude JSONL parsing
# ---------------------------------------------------------------------------


def _parse_claude_events(events: list[dict[str, Any]]) -> TranscriptState:
    if not events:
        return TranscriptState(source="claude")
    messages = _refine_messages(_claude_messages(events))
    usage = _claude_token_usage(events)
    reply = messages[-1].text if messages and messages[-1].role == "agent" else ""
    working = bool(messages) and messages[-1].role == "user"
    return TranscriptState(
        reply=reply,
        working=working,
        messages=tuple(messages),
        token_usage=usage,
        source="claude",
    )


def _claude_messages(events: list[dict[str, Any]]) -> list[TranscriptMessage]:
    messages: list[TranscriptMessage] = []
    for evt in events:
        if evt.get("type") not in ("user", "assistant"):
            continue
        if evt.get("isMeta"):  # system-injected reminders, not real turns
            continue
        message = evt.get("message", {})
        if not isinstance(message, dict):
            continue
        content = message.get("content", [])
        if isinstance(content, list):
            text = _join_claude_content(content)
        else:
            text = str(content)
        if not text or not text.strip() or _is_claude_boilerplate(text):
            continue
        messages.append(
            TranscriptMessage(
                role="user" if message.get("role") == "user" else "agent",
                text=text,
                timestamp_ms=_ts_ms(evt),
                source_id=str(evt.get("uuid", "")),
            )
        )
    return messages


def _claude_token_usage(events: list[dict[str, Any]]) -> TokenUsage | None:
    for evt in reversed(events):
        if evt.get("type") != "assistant":
            continue
        message = evt.get("message", {})
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        inp = int(usage.get("input_tokens", 0))
        out = int(usage.get("output_tokens", 0))
        cache = int(usage.get("cache_creation_input_tokens", 0)) + int(
            usage.get("cache_read_input_tokens", 0)
        )
        return TokenUsage(
            source="claude",
            model=str(message.get("model", "")),
            input_tokens=inp,
            cached_input_tokens=cache,
            output_tokens=out,
            total_tokens=inp + out + cache,
        )
    return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ts_ms(evt: dict[str, Any]) -> int:
    ts = evt.get("timestamp_ms")
    if ts is not None:
        try:
            return int(ts)
        except (ValueError, TypeError):
            return 0
    ts_str = evt.get("timestamp", "")
    if ts_str:
        try:
            dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass
    return 0


def _join_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in ("text", "input_text", "output_text"):
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(p for p in parts if p)


def _join_claude_content(content: list) -> str:
    parts = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type", "")
            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "tool_use":
                parts.append(f"🛠 {block.get('name', 'tool')}")
            elif btype == "tool_result":
                parts.append("[tool result]")
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(p for p in parts if p)


def _is_claude_boilerplate(text: str) -> bool:
    t = text.strip().lower()
    starts = (
        "<command-name>",
        "<command-message>",
        "<command-args>",
        "<local-command",
        "<system>",
        "<environment_context>",
        "caveat:",
        "login successful",
        "set model to",
    )
    return t.startswith(starts) or len(t) < 2
