from __future__ import annotations

import json
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
        return cls(**values)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def load_closing_quotes(path: Path) -> list[ClosingQuote]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClosingQuoteError(f"cannot load closing quotes from {path}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise ClosingQuoteError("closing quote catalog must be a non-empty JSON list")
    if any(not isinstance(item, dict) for item in raw):
        raise ClosingQuoteError("every closing quote entry must be a JSON object")
    return [ClosingQuote.from_dict(item) for item in raw]


def quote_for_date(path: Path, episode_date: date) -> ClosingQuote:
    quotes = load_closing_quotes(path)
    return quotes[episode_date.toordinal() % len(quotes)]
