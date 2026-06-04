"""Tests for the unified tmux command builder (no tmux server required)."""

from agentboard.core.tmux import Tmux


def test_local_argv_is_plain_tmux():
    t = Tmux(host=None)
    assert t._argv(["list-panes", "-a"]) == ["tmux", "list-panes", "-a"]


def test_remote_argv_wraps_in_ssh_and_quotes():
    t = Tmux(host="h200")
    argv = t._argv(["send-keys", "-t", "work", "-l", "hello world"])
    assert argv[0] == "ssh"
    assert argv[-2] == "h200"
    # The whole tmux command is one shell string; the spaced text is quoted so
    # the remote shell keeps it as a single argument.
    remote = argv[-1]
    assert remote.startswith("tmux send-keys -t work -l ")
    assert "'hello world'" in remote


def test_remote_argv_quotes_injection_attempt():
    t = Tmux(host="box")
    remote = t._argv(["send-keys", "-t", "s", "-l", "a; rm -rf /"])[-1]
    # The dangerous text is fully quoted, not interpreted by the remote shell.
    assert "'a; rm -rf /'" in remote
