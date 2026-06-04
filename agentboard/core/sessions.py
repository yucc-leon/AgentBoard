"""Session discovery and addressing.

A *session* is a tmux session on some machine that is running an agent CLI
(codex, claude, ...) in one of its panes. We identify a session by the pair
``(machine, tmux_session_name)`` and drive it by targeting the session's active
pane — ``tmux send-keys -t <session_name>`` resolves to the active pane, which
is what a human attaching to the session would type into.

Discovery is live: we ask each configured machine's tmux server for its panes
on demand. A short in-process cache keeps remote (SSH) machines from being
hit on every page render.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field

from agentboard.config import MachineConfig
from agentboard.core.tmux import Pane, Tmux
from agentboard.logging import get_logger

logger = get_logger(__name__)

# Substrings that identify an agent CLI in a process's full command line.
_PROC_SIGNATURES: list[tuple[tuple[str, ...], str]] = [
    (("anthropic-ai/claude-code", "claude-code", "/claude ", "claude"), "claude"),
    (("openai/codex", "/codex", "codex"), "codex"),
    (("gemini",), "gemini"),
    (("opencode",), "opencode"),
    (("aider",), "aider"),
]


def _local_process_tree() -> tuple[dict[int, str], dict[int, list[int]]]:
    """Snapshot local processes once: (args_by_pid, children_by_ppid)."""
    args_by_pid: dict[int, str] = {}
    children: dict[int, list[int]] = {}
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,args="],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return args_by_pid, children
    for line in proc.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        args_by_pid[pid] = parts[2] if len(parts) > 2 else ""
        children.setdefault(ppid, []).append(pid)
    return args_by_pid, children


def _cli_from_tree(pid: int, tree: tuple[dict[int, str], dict[int, list[int]]]) -> str | None:
    """Find an agent CLI by scanning a pane's process and its descendants."""
    if pid <= 1:  # never walk from init/kernel — that would match the whole box
        return None
    args_by_pid, children = tree
    seen: set[int] = set()
    stack = [pid]
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        low = args_by_pid.get(p, "").lower()
        for needles, cli in _PROC_SIGNATURES:
            if any(n in low for n in needles):
                return cli
        stack.extend(children.get(p, []))
    return None

# (substring, cli_type, is_agent). First match wins; checked against the pane's
# current command (and, when that's a generic shell/interpreter, the process
# tree is not consulted — we keep it simple and rely on the foreground command).
_CLI_PATTERNS: list[tuple[str, str, bool]] = [
    ("codex", "codex", True),
    ("claude", "claude", True),
    ("gemini", "gemini", True),
    ("opencode", "opencode", True),
    ("aider", "aider", True),
    ("cursor", "cursor", True),
]

# Agent CLIs commonly appear under a generic interpreter name in tmux's
# ``pane_current_command`` (e.g. Codex/Claude run as ``node``). For these we
# can't tell from the command alone — we infer the CLI from recent logs in the
# pane's cwd (local only).
_INTERPRETERS = {"node", "node.exe", "deno", "bun", "python", "python3", "uv"}


def classify_command(command: str) -> tuple[str, bool]:
    """Return ``(cli_type, is_agent)`` for a pane's foreground command (by name)."""
    cmd = command.lower().strip()
    for pattern, cli_type, is_agent in _CLI_PATTERNS:
        if pattern in cmd:
            return cli_type, is_agent
    return "shell", False


def classify_pane(command: str, cwd: str, *, local: bool,
                  codex_home: str = "~/.codex", claude_home: str = "~/.claude"
                  ) -> tuple[str, bool]:
    """Classify a pane, falling back to cwd log inference for interpreters."""
    cli, is_agent = classify_command(command)
    if is_agent:
        return cli, True
    base = command.lower().strip().split("/")[-1]
    if local and base in _INTERPRETERS:
        from agentboard.core.conversations import cli_for_cwd

        inferred = cli_for_cwd(cwd, codex_home, claude_home)
        if inferred:
            return inferred, True
    return cli, is_agent


@dataclass(frozen=True)
class Session:
    """An addressable agent (or shell) session on one machine."""

    machine: str          # config machine name, e.g. "local" or "ascend-rl"
    machine_type: str     # "local" | "ssh"
    host: str | None      # ssh host for remote machines
    name: str             # tmux session name — the addressing key within a machine
    cwd: str
    cli: str              # "codex" | "claude" | ... | "shell"
    is_agent: bool
    active: bool
    pid: int
    windows: int = 1

    @property
    def key(self) -> str:
        """Stable identifier across machines, used in URLs as two path segments."""
        return f"{self.machine}/{self.name}"

    def as_dict(self) -> dict:
        return {
            "machine": self.machine,
            "machine_type": self.machine_type,
            "name": self.name,
            "key": self.key,
            "cwd": self.cwd,
            "cli": self.cli,
            "is_agent": self.is_agent,
            "active": self.active,
            "pid": self.pid,
            "windows": self.windows,
        }


def _sessions_from_panes(mc: MachineConfig, panes: list[Pane]) -> list[Session]:
    """Collapse a machine's panes into one Session per tmux session name.

    Within a session, an agent pane (codex/claude/...) wins over a plain shell,
    so a session is labelled by the agent it hosts when there is one.
    """
    by_name: dict[str, list[Pane]] = {}
    for p in panes:
        by_name.setdefault(p.session, []).append(p)

    host = mc.host if mc.type == "ssh" else None
    local = mc.type == "local"
    codex_home = mc.codex_home or "~/.codex"
    claude_home = mc.claude_home or "~/.claude"

    # Local agent CLIs (codex/claude) usually appear as a generic `node` pane.
    # A one-shot process snapshot lets us read each pane's real command line and
    # tell codex from claude precisely; cwd-log inference is only a fallback.
    tree = _local_process_tree() if local else ({}, {})

    def _classify(p: Pane) -> tuple[str, bool]:
        cli, is_agent = classify_command(p.command)
        if is_agent:
            return cli, is_agent
        if local:
            from_proc = _cli_from_tree(p.pid, tree)
            if from_proc:
                return from_proc, True
        return classify_pane(p.command, p.cwd, local=local,
                             codex_home=codex_home, claude_home=claude_home)

    out: list[Session] = []
    for name, group in by_name.items():
        # Prefer the agent pane; fall back to the active pane, then the first.
        chosen: Pane | None = None
        chosen_cli, chosen_is_agent = "shell", False
        for p in group:
            cli, is_agent = _classify(p)
            if is_agent:
                chosen, chosen_cli, chosen_is_agent = p, cli, True
                break
        if chosen is None:
            chosen = next((p for p in group if p.active), group[0])
            chosen_cli, chosen_is_agent = _classify(chosen)

        windows = len({p.window for p in group})
        out.append(
            Session(
                machine=mc.name,
                machine_type=mc.type,
                host=host,
                name=name,
                cwd=chosen.cwd,
                cli=chosen_cli,
                is_agent=chosen_is_agent,
                active=chosen.active,
                pid=chosen.pid,
                windows=windows,
            )
        )
    out.sort(key=lambda s: (not s.is_agent, s.machine, s.name))
    return out


@dataclass
class SessionRegistry:
    """Discovers sessions across configured machines, with a short TTL cache.

    Local machines are cheap to re-query; remote (SSH) machines are cached for
    ``remote_ttl`` seconds so list/refresh actions don't pay an SSH round-trip
    every time.
    """

    machines: list[MachineConfig]
    remote_ttl: float = 8.0
    _cache: dict[str, tuple[float, list[Session]]] = field(default_factory=dict)

    def _machine(self, name: str) -> MachineConfig | None:
        return next((m for m in self.machines if m.name == name), None)

    def _discover_machine(self, mc: MachineConfig) -> list[Session]:
        if not mc.tmux:
            return []
        host = mc.host if mc.type == "ssh" else None
        try:
            panes = Tmux(host).list_panes()
        except Exception as e:  # never let one machine break the whole list
            logger.warning("Discovery failed for machine %s: %s", mc.name, e)
            return []
        return _sessions_from_panes(mc, panes)

    def list(self, *, refresh: bool = False) -> list[Session]:
        """All sessions across all machines (agents first)."""
        out: list[Session] = []
        now = time.monotonic()
        for mc in self.machines:
            is_remote = mc.type == "ssh"
            cached = self._cache.get(mc.name)
            if (
                not refresh
                and is_remote
                and cached
                and now - cached[0] < self.remote_ttl
            ):
                out.extend(cached[1])
                continue
            sessions = self._discover_machine(mc)
            if is_remote:
                self._cache[mc.name] = (now, sessions)
            out.extend(sessions)
        return out

    def get(self, machine: str, name: str, *, refresh: bool = False) -> Session | None:
        for s in self.list(refresh=refresh):
            if s.machine == machine and s.name == name:
                return s
        return None

    def conversations(self, *, refresh: bool = False) -> list:
        """Recent local JSONL conversations, with accurate liveness.

        Liveness is *not* a blanket cwd match (a directory with K running panes
        would otherwise mark all of its historical logs live). Instead, for each
        (cwd, cli) that hosts K live agent panes, only the K most-recently-active
        conversations there are flagged live — those are the ones actually being
        written to right now — and each is linked to one of the live tmux panes.

        Conversations come only from local machines (Codex/Claude logs aren't
        fetched over SSH); remote work is driven through live tmux sessions.
        """
        import os
        from collections import defaultdict

        from agentboard.core.conversations import (
            Conversation,
            discover_conversations,
            discover_remote_conversations,
        )

        live = self.list(refresh=refresh)

        def _norm(p: str) -> str:
            return os.path.normpath(os.path.expanduser(p)).rstrip("/")

        # (machine, cwd, cli) -> queue of live tmux session names to hand out.
        live_panes: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for s in live:
            if s.is_agent:
                live_panes[(s.machine, _norm(s.cwd), s.cli)].append(s.name)

        # Gather conversations from every machine: local logs directly, remote
        # logs over SSH (best-effort — a dead remote yields nothing, not an error).
        collected: list[Conversation] = []
        for mc in self.machines:
            codex_home = mc.codex_home or "~/.codex"
            claude_home = mc.claude_home or "~/.claude"
            if mc.type == "local":
                found = discover_conversations(codex_home, claude_home)
                found = [
                    Conversation(cli=c.cli, cwd=c.cwd, session_id=c.session_id,
                                 path=c.path, title=c.title,
                                 last_activity_ms=c.last_activity_ms, machine=mc.name)
                    for c in found
                ]
            elif mc.host:
                found = discover_remote_conversations(
                    mc.host, mc.name, codex_home, claude_home
                )
            else:
                found = []
            collected.extend(found)

        groups: dict[tuple[str, str, str], list[Conversation]] = defaultdict(list)
        for c in collected:
            groups[(c.machine, _norm(c.cwd), c.cli)].append(c)

        out: list[Conversation] = []
        for gkey, convs in groups.items():
            convs.sort(key=lambda c: c.last_activity_ms, reverse=True)
            names = list(live_panes.get(gkey, []))
            for i, c in enumerate(convs):
                live_name = names[i] if i < len(names) else None
                out.append(
                    Conversation(
                        cli=c.cli, cwd=c.cwd, session_id=c.session_id, path=c.path,
                        title=c.title, last_activity_ms=c.last_activity_ms,
                        machine=c.machine, live_tmux=live_name,
                    )
                )
        out.sort(key=lambda c: c.last_activity_ms, reverse=True)
        return out

    def find_conversation(self, machine: str, cli: str, session_id: str):
        """Locate one conversation on a specific machine (local or remote)."""
        from agentboard.core.conversations import (
            discover_conversations,
            discover_remote_conversations,
        )

        mc = self._machine(machine)
        if mc is None:
            return None
        codex_home = mc.codex_home or "~/.codex"
        claude_home = mc.claude_home or "~/.claude"
        if mc.type == "local":
            convs = discover_conversations(codex_home, claude_home, limit=400, since_days=3650)
        elif mc.host:
            convs = discover_remote_conversations(
                mc.host, mc.name, codex_home, claude_home, limit=400, since_days=3650
            )
        else:
            return None
        for c in convs:
            if c.cli == cli and c.session_id == session_id:
                return c
        return None

    def conversation_transcript(self, conv):
        """Parsed transcript for a conversation — file read locally, SSH cat remotely."""
        from agentboard.core.conversations import read_remote_jsonl
        from agentboard.core.transcript import parse_jsonl_file, parse_jsonl_text

        mc = self._machine(conv.machine)
        if mc and mc.type == "ssh" and mc.host:
            return parse_jsonl_text(read_remote_jsonl(mc.host, conv.path), conv.cli)
        return parse_jsonl_file(conv.path, conv.cli)

    def tmux_for(self, machine: str) -> Tmux | None:
        """A :class:`Tmux` bound to the named machine, or None if unknown."""
        mc = self._machine(machine)
        if mc is None:
            return None
        host = mc.host if mc.type == "ssh" else None
        return Tmux(host)
