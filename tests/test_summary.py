"""Tests for the lean per-session summary layer (cache + model, no LLM call)."""

from agentboard.config import Config, WorkspaceConfig
from agentboard.core.transcript import TranscriptMessage, TranscriptState
from agentboard.intelligence.summary import (
    SessionCard,
    _save_card,
    cached_card,
    fingerprint,
)


def _cfg(tmp_path) -> Config:
    return Config(workspace=WorkspaceConfig(data_dir=str(tmp_path)))


def _state(n=2):
    msgs = tuple(
        TranscriptMessage(role="user" if i % 2 == 0 else "agent", text=f"m{i}",
                          timestamp_ms=i)
        for i in range(n)
    )
    return TranscriptState(messages=msgs, source="codex")


def test_fingerprint_changes_with_growth():
    assert fingerprint(_state(2)) != fingerprint(_state(3))
    assert fingerprint(_state(2)) == fingerprint(_state(2))


def test_cache_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    state = _state(2)
    card = SessionCard(title="t", summary="s", open_items=["x"],
                       fingerprint=fingerprint(state))
    _save_card(cfg, "local/work", card)

    got = cached_card(cfg, "local/work")
    assert got is not None and got.title == "t" and got.open_items == ["x"]


def test_cache_invalidated_when_transcript_grows(tmp_path):
    cfg = _cfg(tmp_path)
    state = _state(2)
    card = SessionCard(title="t", fingerprint=fingerprint(state))
    _save_card(cfg, "local/work", card)

    # Same key, but the conversation grew → stale, should not be returned.
    assert cached_card(cfg, "local/work", _state(5)) is None
    # Matching fingerprint → still fresh.
    assert cached_card(cfg, "local/work", _state(2)) is not None


def test_missing_card_returns_none(tmp_path):
    assert cached_card(_cfg(tmp_path), "local/nope") is None
