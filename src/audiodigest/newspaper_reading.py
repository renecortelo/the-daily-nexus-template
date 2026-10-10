"""Bounded, owner-only reading copy of an already approved newspaper.

No generation, source passages, URLs, mailbox/story IDs or local paths belong
in this projection. Missing/legacy artifacts retain the original PDF view.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from audiodigest.models import DataValidationError, NewspaperIssue
from audiodigest.newspaper import is_legacy_script_style_issue

MAX_READING_BYTES = 128 * 1024


def approved_reading_copy(
    path: Path, *, expected: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Caller must establish ready/published state and the final artifact path."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_READING_BYTES + 1)
        if len(raw) > MAX_READING_BYTES:
            return None  # Never crop approved prose to fit the transport.
        data = json.loads(raw)
        if not isinstance(data, dict) or (expected is not None and data != expected):
            return None
        issue = NewspaperIssue.from_dict(data)
        if (
            is_legacy_script_style_issue(issue)
            or len(issue.data_points) > 30
            or any(
                len(article.bullet_points) > 20 or len(article.highlights) > 20
                for article in issue.articles
            )
        ):
            return None

        def item_copy(item):
            return {"label": item.label, "value": item.value, "detail": item.detail}

        result = {
            "version": 1,
            "headline": issue.headline,
            "deck": issue.deck,
            "lead": issue.lead,
            "kicker": issue.kicker,
            "pullQuote": issue.pull_quote,
            "articles": [
                {
                    "title": article.title,
                    "section": article.section_label,
                    "standfirst": article.standfirst,
                    "body": article.body,
                    "bullets": article.bullet_points,
                    "highlights": [text for text in article.highlights if text in article.body],
                }
                for article in issue.articles
            ],
            "executive": [item_copy(item) for item in issue.executive_summary],
            "visuals": [
                {
                    "kind": visual.kind,
                    "title": visual.title,
                    "caption": visual.caption,
                    "items": [item_copy(item) for item in visual.items],
                }
                for visual in issue.visuals
            ],
            "briefs": [brief.text for brief in issue.briefs],
            "dataPoints": issue.data_points,
        }
        return result if len(json.dumps(result).encode("utf-8")) <= MAX_READING_BYTES else None
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        AttributeError,
        RecursionError,
        DataValidationError,
    ):
        return None
