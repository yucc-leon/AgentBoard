"""Conversation discovery from CLI-native JSONL logs.

A *conversation* is one agent session recorded on disk under ``~/.codex`` or
``~/.claude`` — independent of whether it's currently running in tmux. This is
what lets the hub show *all* your conversations (to locate and summarize them),
not just the ones alive in a pane right now.

Liveness is layered on separately (see :mod:`agentboard.core.sessions`): a
conversation whose working directory matches a live tmux pane can be driven; the
rest are read-only but resumable.

Metadata is extracted cheaply — we read only the head of each file to get the
cwd and a title (the first user message), and use file mtime for recency — so
listing dozens of logs stays fast.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from agentboard.logging import get_logger

logger = get_logger(__name__)

# Only surface logs touched within this window, capped to a sane count.
_DEFAULT_SINCE_DAYS = 30
_DEFAULT_LIMIT = 60
_HEAD_LINES = 80  # how far into a file we look for the first user message


@dataclass(frozen=True)
class Conversation:
    """An on-disk agent conversation."""

    cli: str               # "codex" | "claude"
    cwd: str
    session_id: str
    path: str              # JSONL file path
    title: str
    last_activity_ms: int
    machine: str = "local"
    live_tmux: str | None = None   # tmux session name if currently running

    @property
    def project(self) -> str:
        return Path(self.cwd).name if self.cwd else ""

    @property
    def key(self) -> str:
        return f"{self.cli}/{self.session_id}"

    def as_dict(self) -> dict:
        return {
            "cli": self.cli,
            "cwd": self.cwd,
            "project": self.project,
            "session_id": self.session_id,
            "path": self.path,
            "title": self.title,
            "last_activity_ms": self.last_activity_ms,
            "machine": self.machine,
            "live_tmux": self.live_tmux,
        }


def _norm(path: str) -> str:
    return os.path.normpath(os.path.expanduser(path)).rstrip("/")


def _iter_text(blocks) -> str:
    if isinstance(blocks, str):
        return blocks
    if isinstance(blocks, list):
        out = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") in ("text", "input_text", "output_text"):
                out.append(str(b.get("text", "")))
            elif isinstance(b, str):
                out.append(b)
        return " ".join(p for p in out if p)
    return ""


def _looks_like_boilerplate(text: str) -> bool:
    t = text.strip().lower()
    if t.startswith(("<command-name>", "<command-message>", "<local-command",
                     "<environment_context>", "caveat:", "# agents.md")):
        return True
    # A JSON/data payload injected as the first turn — not a human prompt.
    if t.startswith(("{", "[")) and len(t) > 40:
        return True
    return len(t) < 2


_TITLE_STRIP = re.compile(r"^[\s>#*`\-]+")
# Split after a CJK terminator (no trailing space in Chinese), or an ASCII one
# followed by whitespace/end — so "AGENTS.md" / "1.7B" don't split.
_SENTENCE_END = re.compile(r"(?<=[。！？])|(?<=[.!?])(?=\s|$)")


def _clean_title(text: str) -> str:
    """A readable list label: the first real sentence of the user's message,
    minus leading markdown/quote/list markers. This is the fallback shown when
    no LLM is configured (the LLM quick-title supersedes it when available)."""
    t = text.strip()
    for line in t.splitlines():           # first non-empty, de-marked line
        line = _TITLE_STRIP.sub("", line).strip()
        if line:
            t = line
            break
    return _SENTENCE_END.split(t, maxsplit=1)[0].strip()[:80] if t else ""


def _codex_meta(path: Path) -> Conversation | None:
    cwd = ""
    session_id = ""
    title = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > _HEAD_LINES and title:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = o.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {}
                if o.get("type") == "session_meta":
                    cwd = payload.get("cwd", cwd)
                    session_id = payload.get("id", session_id)
                if not title:
                    role = payload.get("role", "")
                    ptype = payload.get("type", "")
                    if role == "user" or ptype == "user_command":
                        t = _iter_text(payload.get("content", payload.get("text", "")))
                        if t and not _looks_like_boilerplate(t):
                            title = _clean_title(t)
    except OSError:
        return None
    if not session_id:
        session_id = path.stem
    return Conversation(
        cli="codex",
        cwd=cwd,
        session_id=session_id,
        path=str(path),
        title=(title or "(no prompt yet)")[:120],
        last_activity_ms=int(path.stat().st_mtime * 1000),
    )


def _claude_meta(path: Path) -> Conversation | None:
    cwd = ""
    session_id = path.stem
    title = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > _HEAD_LINES and title:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not cwd and o.get("cwd"):
                    cwd = o["cwd"]
                if o.get("sessionId"):
                    session_id = o["sessionId"]
                if not title and o.get("type") == "user" and isinstance(o.get("message"), dict):
                    t = _iter_text(o["message"].get("content", ""))
                    if t and not _looks_like_boilerplate(t):
                        title = _clean_title(t)
    except OSError:
        return None
    return Conversation(
        cli="claude",
        cwd=cwd,
        session_id=session_id,
        path=str(path),
        title=(title or "(no prompt yet)")[:120],
        last_activity_ms=int(path.stat().st_mtime * 1000),
    )


def discover_conversations(
    codex_home: str = "~/.codex",
    claude_home: str = "~/.claude",
    *,
    limit: int = _DEFAULT_LIMIT,
    since_days: int = _DEFAULT_SINCE_DAYS,
) -> list[Conversation]:
    """List recent local conversations across Codex and Claude, newest first."""
    cutoff = time.time() - since_days * 86400
    paths: list[tuple[Path, str]] = []

    codex = Path(os.path.expanduser(codex_home))
    for sub in ("sessions", "rollouts"):
        d = codex / sub
        if d.exists():
            paths += [(p, "codex") for p in d.rglob("*.jsonl")]

    claude = Path(os.path.expanduser(claude_home)) / "projects"
    if claude.exists():
        # Skip Claude subagent sidechain logs ("agent-*.jsonl"): they are sub-tasks
        # of a real session (and carry the parent's id), not standalone chats.
        paths += [
            (p, "claude") for p in claude.rglob("*.jsonl")
            if not p.name.startswith("agent-")
        ]

    # Newest first, recent only, capped.
    def mtime(item: tuple[Path, str]) -> float:
        try:
            return item[0].stat().st_mtime
        except OSError:
            return 0.0

    paths = [it for it in paths if mtime(it) >= cutoff]
    paths.sort(key=mtime, reverse=True)
    paths = paths[:limit]

    out: list[Conversation] = []
    for path, cli in paths:
        conv = _codex_meta(path) if cli == "codex" else _claude_meta(path)
        if conv:
            out.append(conv)
    return _dedupe(out)


def _dedupe(convs: list[Conversation]) -> list[Conversation]:
    """One entry per (cli, session_id) — keep the most-recently-active file.

    Resumes/compactions can write several files under one session id; without
    this they'd each show as a separate (often same-titled) conversation.
    """
    best: dict[tuple[str, str], Conversation] = {}
    for c in convs:
        key = (c.cli, c.session_id)
        cur = best.get(key)
        if cur is None or c.last_activity_ms > cur.last_activity_ms:
            best[key] = c
    return sorted(best.values(), key=lambda c: c.last_activity_ms, reverse=True)


# ---------------------------------------------------------------------------
# Remote discovery (over SSH)
# ---------------------------------------------------------------------------

_SSH_OPTS = ["-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
             "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]

# A self-contained scanner piped to the remote python3 over stdin. It mirrors
# the local head-parse (cwd / id / first-user-message title) and emits one JSON
# object per conversation. Kept dependency-free so the remote needs only python3.
# NOTE: this is a raw script body — config is prepended as a header at call time
# (we can't use str.format here because the script is full of ``{`` literals).
_REMOTE_SCAN_BODY = r'''
import json, os, glob, sys, time
cutoff = time.time() - since_days*86400

def head_lines(path, n=80):
    out=[]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i,l in enumerate(f):
                if i>=n: break
                out.append(l)
    except OSError: pass
    return out

def itext(blocks):
    if isinstance(blocks,str): return blocks
    if isinstance(blocks,list):
        r=[]
        for b in blocks:
            if isinstance(b,dict) and b.get("type") in ("text","input_text","output_text"):
                r.append(str(b.get("text","")))
            elif isinstance(b,str): r.append(b)
        return " ".join(x for x in r if x)
    return ""

def boiler(t):
    t=t.strip().lower()
    if t.startswith(("<command-name>","<command-message>","<local-command","<environment_context>","caveat:","# agents.md")): return True
    if t[:1] in ("{","[") and len(t)>40: return True
    return len(t)<2

import re as _re
def clean(t):
    for line in t.splitlines():
        line=_re.sub(r"^[\s>#*`\-]+","",line).strip()
        if line: t=line; break
    return _re.split(r"(?<=[。！？])|(?<=[.!?])(?=\s|$)",t,1)[0].strip()[:80]

files=[]
for sub in ("sessions","rollouts"):
    files += [(p,"codex") for p in glob.glob(os.path.join(os.path.expanduser(codex_home),sub,"**","*.jsonl"),recursive=True)]
files += [(p,"claude") for p in glob.glob(os.path.join(os.path.expanduser(claude_home),"projects","**","*.jsonl"),recursive=True)
          if not os.path.basename(p).startswith("agent-")]
def mt(it):
    try: return os.path.getmtime(it[0])
    except OSError: return 0
files=[f for f in files if mt(f)>=cutoff]
files.sort(key=mt,reverse=True)
files=files[:limit]
_seen=set()
for path,cli in files:
    cwd=""; sid=os.path.splitext(os.path.basename(path))[0]; title=""
    for l in head_lines(path):
        l=l.strip()
        if not l: continue
        try: o=json.loads(l)
        except Exception: continue
        if cli=="codex":
            p=o.get("payload",{}) if isinstance(o.get("payload"),dict) else {}
            if o.get("type")=="session_meta":
                cwd=p.get("cwd",cwd); sid=p.get("id",sid)
            if not title and (p.get("role")=="user" or p.get("type")=="user_command"):
                t=itext(p.get("content",p.get("text","")))
                if t and not boiler(t): title=clean(t)
        else:
            if not cwd and o.get("cwd"): cwd=o["cwd"]
            if o.get("sessionId"): sid=o["sessionId"]
            if not title and o.get("type")=="user" and isinstance(o.get("message"),dict):
                t=itext(o["message"].get("content",""))
                if t and not boiler(t): title=clean(t)
    dk=(cli,sid)
    if dk in _seen: continue   # one entry per session id (skip resume/subagent dups)
    _seen.add(dk)
    print(json.dumps({"cli":cli,"cwd":cwd,"session_id":sid,"path":path,
                      "title":(title or "(no prompt yet)")[:120],
                      "mtime_ms":int(mt((path,cli))*1000)}))
'''


def discover_remote_conversations(
    host: str,
    machine: str,
    codex_home: str = "~/.codex",
    claude_home: str = "~/.claude",
    *,
    limit: int = _DEFAULT_LIMIT,
    since_days: int = _DEFAULT_SINCE_DAYS,
) -> list[Conversation]:
    """Discover conversations on a remote machine by running a scanner over SSH.

    Requires ``python3`` on the remote. Returns [] on any failure — a remote
    machine that's down must never break the local listing.
    """
    import subprocess

    header = (
        f"codex_home, claude_home, limit, since_days = "
        f"{codex_home!r}, {claude_home!r}, {int(limit)}, {int(since_days)}\n"
    )
    script = header + _REMOTE_SCAN_BODY
    try:
        proc = subprocess.run(
            ["ssh", *_SSH_OPTS, host, "python3 -"],
            input=script, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Remote conversation scan failed on %s: %s", host, e)
        return []
    if proc.returncode != 0:
        logger.warning("Remote scan on %s exited %d: %s", host, proc.returncode,
                       proc.stderr.strip()[:200])
        return []

    out: list[Conversation] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(Conversation(
            cli=d.get("cli", ""), cwd=d.get("cwd", ""),
            session_id=d.get("session_id", ""), path=d.get("path", ""),
            title=d.get("title", ""), last_activity_ms=int(d.get("mtime_ms", 0)),
            machine=machine,
        ))
    return out


def read_remote_jsonl(host: str, path: str) -> str:
    """Cat a remote JSONL file over SSH (for transcript parsing)."""
    import shlex
    import subprocess

    try:
        proc = subprocess.run(
            ["ssh", *_SSH_OPTS, host, f"cat {shlex.quote(path)}"],
            capture_output=True, text=True, timeout=30,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def conversation_by_id(
    cli: str,
    session_id: str,
    codex_home: str = "~/.codex",
    claude_home: str = "~/.claude",
) -> Conversation | None:
    """Find a single conversation by its id (used by the read/resume page)."""
    for conv in discover_conversations(codex_home, claude_home, limit=400, since_days=3650):
        if conv.cli == cli and conv.session_id == session_id:
            return conv
    return None


def cli_for_cwd(
    cwd: str,
    codex_home: str = "~/.codex",
    claude_home: str = "~/.claude",
    *,
    max_age_s: float = 1800,
) -> str | None:
    """Infer which agent CLI is active in a directory from its recent logs.

    Used to classify tmux panes whose foreground command is a generic
    interpreter (e.g. ``node``) rather than ``codex``/``claude`` literally.
    Returns the cli with the most recently modified matching log, or None.
    """
    target = _norm(cwd)
    now = time.time()
    best_cli: str | None = None
    best_mtime = 0.0

    codex = Path(os.path.expanduser(codex_home))
    for sub in ("sessions", "rollouts"):
        d = codex / sub
        if not d.exists():
            continue
        for p in d.rglob("*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if now - m > max_age_s or m <= best_mtime:
                continue
            conv = _codex_meta(p)
            if conv and _norm(conv.cwd) == target:
                best_cli, best_mtime = "codex", m

    claude = Path(os.path.expanduser(claude_home)) / "projects"
    if claude.exists():
        for p in claude.rglob("*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if now - m > max_age_s or m <= best_mtime:
                continue
            conv = _claude_meta(p)
            if conv and _norm(conv.cwd) == target:
                best_cli, best_mtime = "claude", m

    return best_cli
