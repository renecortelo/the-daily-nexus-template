from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ClosingQuoteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ClosingQuote:
    text: str
    author: str
    source_url: str
    topics: tuple[str, ...] = ()

    @property
    def quote_id(self) -> str:
        identity = self.text + "\0" + self.author
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClosingQuote:
        values = {}
        for key in ("text", "author", "source_url"):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ClosingQuoteError(f"closing quote {key!r} must be a non-empty string")
            values[key] = value.strip()
        parts = urlsplit(values["source_url"])
        if parts.scheme != "https" or not parts.hostname:
            raise ClosingQuoteError("closing quote source_url must use public HTTPS")
        topics = data.get("topics", [])
        if not isinstance(topics, list) or any(
            not isinstance(topic, str) or topic not in TOPIC_TERMS for topic in topics
        ):
            raise ClosingQuoteError("closing quote topics must use known topic labels")
        return cls(**values, topics=tuple(dict.fromkeys(topics)))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "quote_id": self.quote_id}


TOPIC_TERMS = {
    "technology": ("technology", "software", "computing", "ai", "model", "cloud", "robot"),
    "data": ("database", "analytics", "pipeline", "etl", "warehouse", "data engineering", "sql"),
    "science": ("science", "scientific", "research", "study", "discovery", "experiment"),
    "learning": ("education", "learning", "knowledge", "school", "university", "training"),
    "change": ("change", "launch", "innovation", "new", "reform", "transition"),
    "judgment": ("evidence", "decision", "risk", "uncertainty", "accuracy", "verification"),
    "business": ("business", "company", "market", "investment", "economy", "trade", "revenue"),
    "leadership": ("government", "policy", "leader", "election", "management", "regulation"),
    "security": ("security", "privacy", "breach", "attack", "fraud", "cybersecurity"),
    "society": ("community", "society", "public", "people", "health", "rights", "culture"),
    "resilience": ("recovery", "challenge", "resilience", "sport", "effort", "crisis"),
}
RECENT_QUOTE_WINDOW = 30


def quote_ids_from_episodes(
    quotes: Sequence[ClosingQuote],
    episodes: Sequence[dict[str, Any]],
) -> list[str]:
    """Read IDs from new metadata, or unambiguous legacy source references.

    Shared book URLs are deliberately not treated as a unique quotation.
    Input episodes must be ordered newest publication first.
    """
    known_ids = {quote.quote_id for quote in quotes}
    result = []
    for episode in episodes[:RECENT_QUOTE_WINDOW]:
        if episode.get("status") not in {"published", "completed", "staged"}:
            continue
        selected = episode.get("closingQuoteId", episode.get("closing_quote_id", ""))
        if isinstance(selected, str) and selected in known_ids:
            result.append(selected)
            continue
        notes = episode.get("references", episode.get("show_notes", []))
        if not isinstance(notes, list):
            continue
        matches = [
            quote.quote_id
            for quote in quotes
            if any(quote.source_url in str(note) for note in notes)
        ]
        if len(matches) == 1:
            result.append(matches[0])
    return result


def select_closing_quote(
    quotes: Sequence[ClosingQuote],
    episode_date: date,
    *,
    stories: Sequence[dict[str, Any]] = (),
    recent_ids: Sequence[str] = (),
    execution_key: str = "",
    reserved_id: str = "",
) -> ClosingQuote:
    """Choose locally: recent-history exclusion, topic relevance, stable tie break."""
    if not quotes:
        raise ClosingQuoteError("closing quote catalog must not be empty")
    by_id = {quote.quote_id: quote for quote in quotes}
    if reserved_id:
        if reserved_id not in by_id:
            raise ClosingQuoteError("reserved closing quote is no longer in the catalog")
        return by_id[reserved_id]
    recent = list(recent_ids[:RECENT_QUOTE_WINDOW])
    candidates = [quote for quote in quotes if quote.quote_id not in recent]
    if not candidates:
        # A custom catalog smaller than the window must still work: reuse the
        # least recently used entry, never silently fail an otherwise valid run.
        oldest = max(recent.index(quote.quote_id) for quote in quotes)
        candidates = [quote for quote in quotes if recent.index(quote.quote_id) == oldest]
    recent_authors = {by_id[item].author for item in recent[:3] if item in by_id}
    varied = [quote for quote in candidates if quote.author not in recent_authors]
    if varied:
        candidates = varied
    themes: Counter[str] = Counter()
    news = [story for story in stories if story.get("section") != "TIH: Today in History"]
    for story in news or stories:
        text = " ".join(
            (
                str(story.get("section", "")),
                str(story.get("headline", "")),
                " ".join(str(fact) for fact in story.get("facts", [])),
            )
        ).casefold()
        for topic, terms in TOPIC_TERMS.items():
            if any(re.search(r"\b" + re.escape(term) + r"\b", text) for term in terms):
                themes[topic] += 1
    seed = episode_date.isoformat() + "\0" + execution_key
    return max(
        candidates,
        key=lambda quote: (
            sum(themes[topic] for topic in quote.topics),
            hashlib.sha256((seed + quote.quote_id).encode("utf-8")).hexdigest(),
        ),
    )


def load_closing_quotes(path: Path) -> list[ClosingQuote]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClosingQuoteError(f"cannot load closing quotes from {path}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise ClosingQuoteError("closing quote catalog must be a non-empty JSON list")
    if any(not isinstance(item, dict) for item in raw):
        raise ClosingQuoteError("every closing quote entry must be a JSON object")
    quotes = [ClosingQuote.from_dict(item) for item in raw]
    if len({" ".join(quote.text.casefold().split()) for quote in quotes}) != len(quotes):
        raise ClosingQuoteError("closing quote catalog contains duplicate quotations")
    return quotes


def quote_for_date(path: Path, episode_date: date) -> ClosingQuote:
    # Compatibility for callers with no story/history context. Production runs
    # use select_closing_quote with evidence and publication history.
    return select_closing_quote(load_closing_quotes(path), episode_date)
