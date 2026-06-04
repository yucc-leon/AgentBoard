"""Tests for transcript parsing (Codex / Claude JSONL + screen fallback)."""

from pathlib import Path

from agentboard.core.transcript import (
    _parse_claude_events,
    _parse_codex_events,
    _read_jsonl_events,
    parse_screen,
)

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_parse_codex_event_msg_format():
    events = [
        {"type": "session_meta", "payload": {"cwd": "/p", "model": "gpt"}},
        {"type": "event_msg", "payload": {"role": "user",
            "content": [{"type": "text", "text": "compare GRPO and GSPO"}]}},
        {"type": "response_item", "payload": {"type": "tool_call", "name": "shell"}},
        {"type": "event_msg", "payload": {"role": "assistant",
            "content": [{"type": "text", "text": "Here is the comparison"}]}},
    ]
    state = _parse_codex_events(events)
    assert state.source == "codex"
    roles = [m.role for m in state.messages]
    assert "user" in roles and "agent" in roles
    assert state.messages[0].text == "compare GRPO and GSPO"
    assert state.working is False  # agent spoke last
    assert state.reply == "Here is the comparison"


def test_parse_codex_response_item_message():
    events = [
        {"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "fix the bug"}]}},
    ]
    state = _parse_codex_events(events)
    assert state.messages[-1].role == "user"
    assert state.working is True  # user spoke last, awaiting reply


def test_parse_claude_events():
    events = [
        {"type": "user", "uuid": "u1", "message": {"role": "user",
            "content": [{"type": "text", "text": "tune the css"}]}},
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant",
            "model": "claude", "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 10, "output_tokens": 3}}},
    ]
    state = _parse_claude_events(events)
    assert state.source == "claude"
    assert [m.role for m in state.messages] == ["user", "agent"]
    assert state.token_usage and state.token_usage.total_tokens == 13


def test_claude_boilerplate_filtered():
    events = [
        {"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": "<command-name>/clear</command-name>"}]}},
        {"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": "real question"}]}},
    ]
    state = _parse_claude_events(events)
    texts = [m.text for m in state.messages]
    assert texts == ["real question"]


def test_parse_screen_fallback():
    state = parse_screen("line one\n\x1b[31mWorking\x1b[0m on task\nesc")
    assert state.source == "screen"
    assert state.working is True
    assert "Working on task" in state.reply


def test_example_files_parse_without_error():
    codex = _parse_codex_events(_read_jsonl_events(EXAMPLES / "codex_short.jsonl"))
    assert len(codex.messages) >= 1
    claude = _parse_claude_events(_read_jsonl_events(EXAMPLES / "claude_compact.jsonl"))
    assert len(claude.messages) >= 1
