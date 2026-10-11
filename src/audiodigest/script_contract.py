"""Deterministic script shape and trusted closing assembly; no model calls."""

from __future__ import annotations

import re
from typing import Any

from audiodigest.closing_quotes import ClosingQuote
from audiodigest.models import DataValidationError, DialogueTurn, EpisodeScript, Story

CLOSING_COMMENT_MAX_WORDS = 25
CONVERSATION_TURN_MAX_WORDS = 115


def _script_output_contract(
    stories: list[Story],
    section_order: tuple[str, ...],
    required_story_ids: list[str],
    host_names: list[str],
    dialogue_style: str,
) -> dict[str, Any]:
    """Expose existing deterministic constraints before drafting, not another review.

    No source excerpts, mailbox identities or extra model call enter this plan.
    The existing structural and independent factual validators remain authoritative.
    """
    required = set(required_story_ids)
    conversation = len(host_names) == 2 and dialogue_style == "conversation"
    sections = []
    for name in section_order:
        ids = [story.story_id for story in stories if story.section.value == name]
        if not ids:
            continue
        sections.append({
            "name": name,
            "available_story_ids": ids,
            "required_story_ids": [story_id for story_id in ids if story_id in required],
            "both_hosts_required_if_two_or_more_stories_cited": conversation,
        })
    return {
        "exact_hosts_in_order": host_names,
        "introduction": {
            "introduce_each_active_host": True,
            "editor_credit_occurrences": 1,
            "editor_is_not_a_host": True,
        },
        "sections": sections,
        "maximum_words_per_turn": CONVERSATION_TURN_MAX_WORDS if conversation else None,
        "section_headings_spoken_by_renderer": True,
        "closing_comment_maximum_words": CLOSING_COMMENT_MAX_WORDS,
        "closing_quote_and_source_inserted_by_application": True,
        "closing_comment": {"maximum_turns": 1, "optional": True},
    }


def _bound_closing_comment(script: EpisodeScript, quote: ClosingQuote) -> None:
    """Keep approved quotation intact; never crop an overlong comment mid-sentence.

    This is a non-fatal style guard, not another model call or whole-script retry.
    The subsequent factual review still checks any retained original observation.
    """
    text = script.sign_off_text
    after_quote = text.split(quote.text, 1)[1]
    comment = after_quote.split(quote.author, 1)[-1].lstrip(" \n\"'“”‘’—–-:;.").strip()
    quote_host = next(
        (turn.host for turn in script.sign_off if quote.text in turn.text), script.hosts[0]
    )
    comment_host = script.sign_off[-1].host
    script.sign_off = [DialogueTurn(quote_host, f"{quote.text} — {quote.author}.")]
    if quote.text in comment:
        # A repeated approved quote is not an original observation.
        comment = ""
    if len(comment.split()) > CLOSING_COMMENT_MAX_WORDS:
        first_sentence = re.split(r"(?<=[.!?])\s+", comment, maxsplit=1)[0]
        comment = first_sentence if len(first_sentence.split()) <= CLOSING_COMMENT_MAX_WORDS else ""
        print(
            "Closing observation exceeded its limit; retained complete short copy only.", flush=True
        )
    if comment:
        script.sign_off.append(DialogueTurn(comment_host, comment))


def _normalize_script_closing(
    data: dict[str, Any], quote: ClosingQuote, host_names: list[str],
) -> dict[str, Any]:
    """Assemble trusted attribution locally; the model writes only the observation.

    Saved scripts retain their sign_off schema. Legacy drafts can contribute an
    unambiguous separate observation, but an unidentified quotation is discarded,
    never presented as a new original comment. Source/host validation still follows.
    """
    result = dict(data)
    comments: list[dict[str, str]] = []
    if "closing_comment" in data:
        raw = data["closing_comment"]
        if not isinstance(raw, list) or len(raw) > 1 or any(
            not isinstance(turn, dict) for turn in raw
        ):
            raise ValueError("closing_comment must be a list containing at most one dialogue turn")
        try:
            comments = [DialogueTurn.from_dict(turn).to_dict() for turn in raw]
        except DataValidationError as exc:
            raise ValueError("closing_comment requires a nonempty host and text") from exc
    else:
        legacy = data.get("sign_off", [])
        if isinstance(legacy, str):
            legacy = [{"host": host_names[0], "text": legacy}]
        if isinstance(legacy, list) and legacy and all(
            isinstance(turn, dict) and isinstance(turn.get("text"), str) for turn in legacy
        ):
            # Old output used quotation first, optional observation second.
            if len(legacy) > 1:
                comments = legacy[1:]
            elif quote.text in legacy[0]["text"]:
                tail = legacy[0]["text"].split(quote.text, 1)[1]
                if quote.author in tail:
                    comment = tail.split(quote.author, 1)[1].lstrip(" \n\"'“”‘’—–-:;.").strip()
                    if comment:
                        comments = [{"host": legacy[0].get("host", host_names[0]),
                                     "text": comment}]
    result["sign_off"] = [
        {"host": host_names[0], "text": f"{quote.text} — {quote.author}."}, *comments,
    ]
    notes = data.get("show_notes", [])
    if isinstance(notes, list) and all(isinstance(note, str) for note in notes):
        notes = list(notes)
        if not any(quote.source_url in note for note in notes):
            notes.append(f"Closing quotation — {quote.author} — {quote.source_url}")
        result["show_notes"] = notes
    return result


def _normalize_script_section_order(
    data: dict[str, Any],
    section_order: tuple[str, ...],
) -> dict[str, Any]:
    """Return model output with valid section objects in canonical episode order."""

    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list):
        return data
    order = {name: index for index, name in enumerate(section_order)}
    indexed_sections = list(enumerate(raw_sections))
    normalized = sorted(
        indexed_sections,
        key=lambda item: (
            order.get(
                str(item[1].get("name", "")) if isinstance(item[1], dict) else "",
                len(order),
            ),
            item[0],
        ),
    )
    if [index for index, _section in normalized] == list(range(len(raw_sections))):
        return data
    return {
        **data,
        "sections": [section for _index, section in normalized],
    }

