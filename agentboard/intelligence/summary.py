"""Lean per-session summarization.

Operates directly on a single session's parsed transcript (see
``agentboard.core.transcript``) — no clustering, no event database. It answers
the two questions the user actually has when juggling many conversations:

  1. *Which* conversation is this?      → a short, recognizable ``title``
  2. What happened and what's pending?  → ``summary`` + ``next_action`` +
                                           ``open_items`` (things possibly missed)

Cards are cached on disk keyed by session, and invalidated by a cheap
fingerprint (message count + last timestamp) so a session is only re-summarized
when its transcript actually grew.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from agentboard.config import Config
from agentboard.core.transcript import TranscriptState
from agentboard.intelligence.llm import LLMClient
from agentboard.logging import get_logger
from agentboard.redaction import redact_text

logger = get_logger(__name__)

_SYSTEM = """\
You summarize a single AI coding-agent conversation for a developer who is \
juggling many parallel sessions. Output ONLY a JSON object with these fields:

  "title":         a SHORT (<= 8 words) human-recognizable label for THIS \
conversation, so the user can pick it out of a list at a glance.
  "summary":       2-4 sentences: what this conversation was about and how far \
it got. Distinguish the original goal from the current state.
  "current_state": one sentence on where things stand right now.
  "next_action":   the single most useful next step. Empty string if truly done.
  "open_items":    a list of unresolved or possibly-OVERLOOKED threads — TODOs \
mentioned but not done, errors left unaddressed, questions the agent asked that \
were never answered, follow-ups the user requested mid-stream and may have \
forgotten. This is the most important field; be thorough but do not invent.
  "confidence":    0.0-1.0, your confidence in this reading.

Ground everything in the transcript. Do not fabricate. If the transcript is too \
thin to tell, say so in summary and use empty arrays."""

_MAX_TRANSCRIPT_CHARS = 14000


class SessionCard(BaseModel):
    """Compact LLM reading of one session."""

    title: str = ""
    summary: str = ""
    current_state: str = ""
    next_action: str = ""
    open_items: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    # cache bookkeeping
    fingerprint: str = ""
    generated_at: str = ""

    def as_dict(self) -> dict:
        return self.model_dump()


def fingerprint(state: TranscriptState) -> str:
    """Cheap content signature; changes only when the transcript grows."""
    msgs = state.messages
    last_ts = msgs[-1].timestamp_ms if msgs else 0
    return f"{len(msgs)}:{last_ts}"


def _format_transcript(state: TranscriptState) -> str:
    """Render recent turns, newest-biased, within a character budget."""
    chunks: list[str] = []
    total = 0
    for msg in reversed(state.messages):
        who = "USER" if msg.role == "user" else "AGENT"
        line = f"[{who}] {msg.text.strip()}"
        if total + len(line) > _MAX_TRANSCRIPT_CHARS:
            break
        chunks.append(line)
        total += len(line)
    chunks.reverse()
    return redact_text("\n\n".join(chunks))


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def _cache_path(config: Config) -> Path:
    return Path(config.workspace.data_dir).expanduser() / "summaries.json"


def _load_cache(config: Config) -> dict[str, dict]:
    path = _cache_path(config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_card(config: Config, key: str, card: SessionCard) -> None:
    path = _cache_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = _load_cache(config)
    cache[key] = card.as_dict()
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write summary cache: %s", e)


# ---------------------------------------------------------------------------
# Quick titles — cheap, list-facing labels (separate from the heavy card)
# ---------------------------------------------------------------------------

_TITLE_SYSTEM = (
    "Give a concise title for a coding-agent conversation: at most 6 words, "
    "no surrounding quotes, no trailing punctuation. Use the conversation's "
    "language. Reply with ONLY the title."
)


def _titles_path(config: Config) -> Path:
    return Path(config.workspace.data_dir).expanduser() / "titles.json"


def _load_titles(config: Config) -> dict[str, str]:
    path = _titles_path(config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def cached_title(config: Config, key: str) -> str | None:
    return _load_titles(config).get(key)


async def quick_title(
    config: Config, key: str, seed_text: str, *, force: bool = False
) -> str | None:
    """A cheap LLM title from a short seed (usually the first user message).

    Cached once per session — the opening message doesn't change, so we don't
    refingerprint. Heavy enough to be worth caching, cheap enough to run across
    a whole list. Returns None if no LLM is configured or the seed is empty.
    """
    seed = (seed_text or "").strip()
    if not seed:
        return None
    if not force:
        existing = cached_title(config, key)
        if existing:
            return existing

    client = LLMClient(config.llm)
    if not client.available:
        return None
    result = await client.chat(
        [
            {"role": "system", "content": _TITLE_SYSTEM},
            {"role": "user", "content": redact_text(seed[:800])},
        ],
        # Generous enough that reasoning models (which spend the budget thinking
        # before emitting) still have room to produce the short title. Input and
        # output stay tiny, so this is far cheaper than a full card.
        max_tokens=600,
        timeout=60.0,
    )
    if not result or not result.get("content"):
        return None
    title = result["content"].strip().strip('"').strip().splitlines()[0][:80]
    if not title:
        return None

    titles = _load_titles(config)
    titles[key] = title
    path = _titles_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write title cache: %s", e)
    return title


def cached_card(
    config: Config, key: str, state: TranscriptState | None = None
) -> SessionCard | None:
    """Return the cached card for a session, if any.

    When ``state`` is given, only return the card if it is still fresh
    (its fingerprint matches the current transcript).
    """
    raw = _load_cache(config).get(key)
    if not raw:
        return None
    try:
        card = SessionCard(**raw)
    except ValidationError:
        return None
    if state is not None and card.fingerprint != fingerprint(state):
        return None
    return card


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def summarize_session(
    config: Config,
    key: str,
    state: TranscriptState,
    *,
    force: bool = False,
) -> SessionCard | None:
    """Generate (or reuse) a SessionCard for one session's transcript.

    Returns None when there is nothing to summarize or no LLM is configured.
    A fresh cached card is reused unless ``force`` is set.
    """
    if not state.messages:
        return None

    if not force:
        fresh = cached_card(config, key, state)
        if fresh:
            return fresh

    client = LLMClient(config.llm)
    if not client.available:
        logger.info("No LLM configured — skipping summary for %s", key)
        return None

    user_prompt = (
        f"Agent CLI: {state.source or 'unknown'}\n"
        f"Conversation transcript (oldest to newest):\n\n{_format_transcript(state)}"
    )
    data = await client.extract_json(_SYSTEM, user_prompt)
    if not data:
        return None

    try:
        card = SessionCard(
            title=str(data.get("title", "")).strip()[:120],
            summary=str(data.get("summary", "")).strip(),
            current_state=str(data.get("current_state", "")).strip(),
            next_action=str(data.get("next_action", "")).strip(),
            open_items=[str(x).strip() for x in (data.get("open_items") or []) if str(x).strip()],
            confidence=float(data.get("confidence", 0.5) or 0.5),
            fingerprint=fingerprint(state),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    except (ValidationError, ValueError, TypeError) as e:
        logger.warning("Bad summary payload for %s: %s", key, e)
        return None

    _save_card(config, key, card)
    return card
