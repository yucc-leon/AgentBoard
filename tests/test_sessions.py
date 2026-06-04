"""Tests for session discovery / grouping (no tmux server required)."""

from agentboard.config import MachineConfig
from agentboard.core.sessions import _sessions_from_panes, classify_command
from agentboard.core.tmux import Pane


def test_classify_command():
    assert classify_command("codex") == ("codex", True)
    assert classify_command("node /usr/bin/claude") == ("claude", True)
    assert classify_command("zsh") == ("shell", False)
    assert classify_command("vim") == ("shell", False)


def _pane(session, window, pane, cmd, active=False, cwd="/tmp"):
    return Pane(session=session, window=window, pane=pane, cwd=cwd,
                command=cmd, active=active, pid=0)


def test_agent_pane_wins_within_a_session():
    mc = MachineConfig(name="local", type="local")
    panes = [
        _pane("work", "0", "0", "zsh", active=True, cwd="/home/x"),
        _pane("work", "1", "0", "codex", cwd="/home/x/proj"),
    ]
    sessions = _sessions_from_panes(mc, panes)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.name == "work"
    assert s.cli == "codex" and s.is_agent is True
    assert s.cwd == "/home/x/proj"  # took the agent pane's cwd
    assert s.windows == 2


def test_shell_only_session_is_not_agent():
    mc = MachineConfig(name="local", type="local")
    panes = [_pane("scratch", "0", "0", "bash", active=True)]
    sessions = _sessions_from_panes(mc, panes)
    assert sessions[0].is_agent is False
    assert sessions[0].cli == "shell"


def test_sessions_sorted_agents_first():
    mc = MachineConfig(name="local", type="local")
    panes = [
        _pane("zshonly", "0", "0", "zsh", active=True),
        _pane("agentbox", "0", "0", "claude", active=True),
    ]
    sessions = _sessions_from_panes(mc, panes)
    assert sessions[0].name == "agentbox"  # agent floats to the top


def test_key_format():
    mc = MachineConfig(name="h200", type="ssh", host="h200")
    panes = [_pane("train", "0", "0", "codex", active=True)]
    s = _sessions_from_panes(mc, panes)[0]
    assert s.key == "h200/train"
    assert s.host == "h200" and s.machine_type == "ssh"
