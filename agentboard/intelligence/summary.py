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

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from agentboard.config import Config
from agentboard.core.transcript import TranscriptMessage, TranscriptState
from agentboard.intelligence.llm import LLMClient
from agentboard.logging import get_logger
from agentboard.redaction import redact_text

logger = get_logger(__name__)

_SYSTEM = """\
You summarize a single AI coding-agent conversation for a developer who is \
juggling many parallel sessions. Output ONLY a JSON object with these fields:

  "title":         a SHORT (<= 8 words) human-recognizable label for THIS \
conversation, so the user can pick it out of a list at a glance.
  "summary":       AT MOST 2-3 sentences, tight: one for the original goal, one \
for how far it got / where it stands (optionally one for a pivotal turn). Detail \
belongs in the other fields — do NOT recap everything here.
  "current_state": one sentence on where things stand right now.
  "next_action":   the single most useful next step. Empty string if truly done.
  "open_items":    a list of unresolved or possibly-OVERLOOKED threads — TODOs \
mentioned but not done, errors left unaddressed, questions the agent asked that \
were never answered, follow-ups the user requested mid-stream and may have \
forgotten. This is the most important field; be thorough but do not invent.
  "key_files":     a list of file paths central to this work (created, edited, \
or repeatedly discussed). Paths only, most important first. Empty if none clear.
  "confidence":    0.0-1.0, your confidence in this reading.

Write every field in the conversation's primary language (if the user writes \
mostly Chinese, answer in Chinese). Keep file paths, code identifiers and \
commands verbatim.

Ground everything in the transcript. Do not fabricate. If the transcript is too \
thin to tell, say so in summary and use empty arrays. The reader will use this \
to RESUME the work, so optimize for fast context recovery."""

_MAX_TRANSCRIPT_CHARS = 14000

# Split after a CJK terminator, or an ASCII terminator that's followed by
# whitespace/end — the latter guard keeps decimals like "1.7B" or "v5.2" intact.
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？])|(?<=[.!?])(?=\s|$)")


def _cap_sentences(text: str, n: int = 3) -> str:
    """Keep at most ``n`` sentences. The model doesn't always honor the prompt's
    length cap on dense conversations; the recap stays scannable while detail
    still lives in current_state/next_action/open_items."""
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p and p.strip()]
    return ("".join(parts[:n]) if len(parts) > n else text).strip()


class SessionCard(BaseModel):
    """Compact LLM reading of one session."""

    title: str = ""
    summary: str = ""
    current_state: str = ""
    next_action: str = ""
    open_items: list[str] = Field(default_factory=list)
    key_files: list[str] = Field(default_factory=list)
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
    """Render the transcript within a character budget.

    If it fits, render the whole thing. Otherwise keep the HEAD (the original
    goal / setup) and the TAIL (the current state) and drop the middle — losing
    the head would erase what the work was originally for, which is exactly what
    a context-recovery summary must not lose.
    """
    lines = [
        f"[{'USER' if m.role == 'user' else 'AGENT'}] {m.text.strip()}"
        for m in state.messages
    ]
    joined = "\n\n".join(lines)
    if len(joined) <= _MAX_TRANSCRIPT_CHARS:
        return redact_text(joined)

    # Reserve ~30% for the opening turns, the rest for the most recent ones.
    head_budget = int(_MAX_TRANSCRIPT_CHARS * 0.3)
    head: list[int] = []
    total = 0
    for i, line in enumerate(lines):
        if total + len(line) > head_budget:
            break
        head.append(i)
        total += len(line)

    tail: list[int] = []
    total = 0
    head_set = set(head)
    for i in range(len(lines) - 1, -1, -1):
        if i in head_set:
            break  # tail has reached the head — whole thing covered
        if total + len(lines[i]) > _MAX_TRANSCRIPT_CHARS - head_budget:
            break
        tail.append(i)
        total += len(lines[i])
    tail.reverse()

    kept = [lines[i] for i in head]
    if tail and (not head or tail[0] > head[-1] + 1):
        kept.append("[… earlier turns omitted …]")
    kept += [lines[i] for i in tail]
    return redact_text("\n\n".join(kept))


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
    "Give a concise title for a coding-agent conversation: at most 8 words, "
    "no surrounding quotes, no trailing punctuation. Capture what the work is "
    "actually about (favor the latest direction over the opening line). Use the "
    "conversation's language. Reply with ONLY the title."
)


def title_seed(state: TranscriptState) -> str:
    """A small, representative sample of a conversation for titling.

    Not the full transcript — we sample user turns at the head, middle and tail
    (weighted toward the tail, since a conversation's current focus matters most
    for a title) plus the latest agent reply for context. Keeps input tiny.
    """
    users = [m for m in state.messages if m.role == "user" and m.text.strip()]
    if not users:
        # No user turns parsed — fall back to whatever text we have.
        return (state.reply or "").strip()[:600]

    picked: list[TranscriptMessage] = []
    seen: set[int] = set()

    def add(m: TranscriptMessage) -> None:
        if id(m) not in seen:
            seen.add(id(m))
            picked.append(m)

    add(users[0])                              # the original ask
    if len(users) >= 5:
        add(users[len(users) // 2])            # something from the middle
    for m in users[-3:]:                       # the recent focus (bias to tail)
        add(m)

    parts = [f"User: {m.text.strip()[:400]}" for m in picked]
    agents = [m for m in state.messages if m.role == "agent" and m.text.strip()]
    if agents:
        parts.append(f"Agent (latest): {agents[-1].text.strip()[:300]}")
    return "\n".join(parts)[:2000]


_TITLE_LOCK = asyncio.Lock()


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
    config: Config, key: str, state: TranscriptState, *, force: bool = False
) -> str | None:
    """A cheap LLM title from a head/middle/tail sample of the conversation.

    Far cheaper than a full card (tiny sampled input, short output), but richer
    than the opening line alone — so conversations that start with "继续"/"ok"
    still get a meaningful title. Cached; returns None if no LLM or no content.
    """
    if not force:
        existing = cached_title(config, key)
        if existing:
            return existing
    seed = title_seed(state).strip()
    if not seed:
        return None

    client = LLMClient(config.llm)
    if not client.available:
        return None
    result = await client.chat(
        [
            {"role": "system", "content": _TITLE_SYSTEM},
            {"role": "user", "content": redact_text(seed)},
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

    # Serialize the read-modify-write so concurrent titling doesn't clobber the
    # cache file (the batch endpoint titles several conversations at once).
    async with _TITLE_LOCK:
        titles = _load_titles(config)
        titles[key] = title
        path = _titles_path(config)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8"
            )
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
            summary=_cap_sentences(str(data.get("summary", "")).strip()),
            current_state=str(data.get("current_state", "")).strip(),
            next_action=str(data.get("next_action", "")).strip(),
            open_items=[str(x).strip() for x in (data.get("open_items") or []) if str(x).strip()],
            key_files=[str(x).strip() for x in (data.get("key_files") or []) if str(x).strip()],
            confidence=float(data.get("confidence", 0.5) or 0.5),
            fingerprint=fingerprint(state),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    except (ValidationError, ValueError, TypeError) as e:
        logger.warning("Bad summary payload for %s: %s", key, e)
        return None

    _save_card(config, key, card)
    return card
