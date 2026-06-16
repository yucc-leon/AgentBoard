"""Unified tmux control — runs locally or over SSH with one code path.

A :class:`Tmux` instance is bound to a machine: ``host=None`` means the local
machine, any other value means ``ssh <host> tmux ...``.

The SSH path quotes every tmux argument with :func:`shlex.quote` and joins them
into a single remote command string. This is essential for ``send-keys -l`` with
text that contains spaces — without quoting, the remote shell would re-split the
text into multiple arguments and the wrong thing would be typed.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from agentboard.logging import get_logger

logger = get_logger(__name__)

# How a session/pane is addressed and described by ``list-panes``.
# Fields are pipe-separated because tmux paths never contain '|'.
_PANE_FORMAT = (
    "#{session_name}|#{window_index}|#{pane_index}|"
    "#{pane_current_path}|#{pane_current_command}|#{pane_active}|#{pane_pid}"
)

_SSH_OPTS = ["-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
             "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]


@dataclass(frozen=True)
class Pane:
    """One tmux pane on one machine."""

    session: str
    window: str
    pane: str
    cwd: str
    command: str
    active: bool
    pid: int

    @property
    def target(self) -> str:
        """Full tmux target specifier ``session:window.pane``."""
        return f"{self.session}:{self.window}.{self.pane}"


class TmuxError(RuntimeError):
    """Raised when a tmux/ssh command fails."""


class Tmux:
    """Control tmux on one machine (local when ``host`` is None, else over SSH)."""

    def __init__(self, host: str | None = None, *, timeout: float = 12.0):
        self.host = host
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Command plumbing
    # ------------------------------------------------------------------

    def _argv(self, args: list[str]) -> list[str]:
        """Build the argv for a tmux invocation, wrapping in ssh if remote."""
        if self.host:
            remote = "tmux " + " ".join(shlex.quote(a) for a in args)
            return ["ssh", *_SSH_OPTS, self.host, remote]
        return ["tmux", *args]

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        argv = self._argv(args)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout
            )
        except FileNotFoundError as e:
            raise TmuxError(f"command not found: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise TmuxError(f"timed out: {' '.join(args)}") from e
        if check and proc.returncode != 0:
            raise TmuxError(proc.stderr.strip() or f"tmux exited {proc.returncode}")
        return proc

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """True if a tmux server is reachable on this machine."""
        try:
            self._run(["list-sessions"], check=False)
            return True
        except TmuxError:
            return False

    def list_panes(self) -> list[Pane]:
        """List every pane on this machine. Empty list if no server / no tmux."""
        try:
            proc = self._run(["list-panes", "-a", "-F", _PANE_FORMAT], check=False)
        except TmuxError as e:
            logger.debug("list_panes failed on %s: %s", self.host or "local", e)
            return []
        if proc.returncode != 0:
            return []

        panes: list[Pane] = []
        for line in proc.stdout.strip().splitlines():
            parts = line.split("|", 6)
            if len(parts) < 7:
                continue
            session, window, pane, cwd, command, active, pid = parts
            panes.append(
                Pane(
                    session=session,
                    window=window,
                    pane=pane,
                    cwd=cwd,
                    command=command,
                    active=active == "1",
                    pid=int(pid) if pid.isdigit() else 0,
                )
            )
        return panes

    def has_session(self, name: str) -> bool:
        proc = self._run(["has-session", "-t", name], check=False)
        return proc.returncode == 0

    def capture(self, target: str, lines: int = 200) -> str:
        """Capture the visible pane plus ``lines`` of scrollback."""
        proc = self._run(
            ["capture-pane", "-p", "-t", target, "-S", f"-{lines}"], check=False
        )
        return proc.stdout if proc.returncode == 0 else ""

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def send(self, target: str, text: str, *, enter: bool = True) -> None:
        """Type ``text`` into a pane as literal keys, optionally pressing Enter.

        Enter is sent as a *separate* ``send-keys`` call so that the agent's
        input box receives the text first and the submit second — sending them
        together races on some TUIs.
        """
        self._run(["send-keys", "-t", target, "-l", text])
        if enter:
            self._run(["send-keys", "-t", target, "Enter"])

    def send_keys(self, target: str, keys: list[str]) -> None:
        """Send raw tmux key names (e.g. ``["C-c"]``, ``["Escape"]``)."""
        self._run(["send-keys", "-t", target, *keys])

    def new_session(self, name: str, cwd: str, command: str | None = None) -> None:
        """Create a detached tmux session, optionally launching a command in it."""
        self._run(["new-session", "-d", "-s", name, "-c", cwd])
        if command:
            target = f"{name}:0.0"
            self.send(target, command, enter=True)

    def kill_session(self, name: str) -> None:
        self._run(["kill-session", "-t", name])
