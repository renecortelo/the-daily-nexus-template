"""Deterministic script shape and trusted closing assembly; no model calls."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from audiodigest.closing_quotes import ClosingQuote
from audiodigest.content import is_tracking_url, minimize_editorial_text, normalize_url
from audiodigest.models import DataValidationError, DialogueTurn, EpisodeScript, Story

CLOSING_COMMENT_MAX_WORDS = 25
CONVERSATION_TURN_MAX_WORDS = 115
BRIEF_OPENING_MAX_BUDGET_WORDS = 1_300


def _brief_introduction(host_names: list[str], episode_date: date) -> list[dict[str, str]]:
    """Application-owned identity only; never shorten or summarize news here.

    The exact AI disclosure remains a separate renderer block. English month names
    are explicit so this English program does not inherit the machine's locale.
    """
    month = (
        "January", "February", "March", "April", "May", "June", "July", "August",
        "September", "October", "November", "December",
    )[episode_date.month - 1]
    introduction = [{"host": host, "text": f"I'm {host}."} for host in host_names]
    introduction[0]["text"] += (
        f" The Daily Nexus, {month} {episode_date.day}, {episode_date.year}, "
        "edited and produced by Dario Novelli."
    )
    return introduction


def _script_output_contract(
    stories: list[Story],
    section_order: tuple[str, ...],
    required_story_ids: list[str],
    host_names: list[str],
    dialogue_style: str,
    *,
    brief_introduction: list[dict[str, str]] | None = None,
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
        "required_fields": ["title", "hosts", "introduction", "sections", "conclusion"],
        "section_fields": ["name", "dialogue", "story_ids"],
        "turn_fields": ["host", "text"],
        "show_notes": "application-owned; return []; source links are assembled from cited stories",
        "exact_hosts_in_order": host_names,
        "introduction": {
            "introduce_each_active_host": True,
            "editor_credit_occurrences": 1,
            "editor_is_not_a_host": True,
            "application_owned": brief_introduction is not None,
            "exact_turns": brief_introduction,
        },
        "sections": sections,
        "maximum_words_per_turn": CONVERSATION_TURN_MAX_WORDS if conversation else None,
        "section_headings_spoken_by_renderer": True,
        "closing_comment_maximum_words": CLOSING_COMMENT_MAX_WORDS,
        "closing_quote_and_source_inserted_by_application": True,
        "closing_comment": {"maximum_turns": 1, "optional": True},
    }


def _script_output_example(contract: dict[str, Any], disclosure: str) -> dict[str, Any]:
    """A valid structural template with real desks/IDs, not misleading placeholders."""
    hosts = contract["exact_hosts_in_order"]
    exact_intro = contract["introduction"].get("exact_turns")
    if exact_intro is not None:
        introduction = [dict(turn) for turn in exact_intro]
    else:
        introduction = [{"host": host, "text": f"I'm {host}."} for host in hosts]
        introduction[0]["text"] += " The Daily Nexus is edited and produced by Dario Novelli."
    return {
        "title": "The Daily Nexus - Month D, YYYY",
        "hosts": list(hosts),
        "introduction": introduction,
        "sections": [{
            "name": row["name"],
            "dialogue": [{"host": host, "text": "Replace with distinct supported narration."}
                         for host in hosts],
            "story_ids": list(row["required_story_ids"] or row["available_story_ids"][:1]),
        } for row in contract["sections"]],
        "conclusion": [{"host": hosts[0], "text": "Thanks for listening."}],
        "closing_comment": [],
        "show_notes": [],
        "disclosure": disclosure,
    }


def _script_source_notes(
    script: EpisodeScript, stories: list[Story], quote: ClosingQuote,
) -> list[str]:
    """Build references from cited records, never from model-authored show notes.

    Preserve first-citation order and distinct URLs. Mail-only stories remain
    identifiable without exposing mailbox IDs, passage text or inventing a link.
    Source lineage and factual verification are still required by the pipeline.
    """
    by_id = {story.story_id: story for story in stories}
    cited_ids = list(dict.fromkeys(
        identifier for section in script.sections for identifier in section.story_ids
    ))
    if any(identifier not in by_id for identifier in cited_ids):
        raise DataValidationError(
            "script cites unsupported story IDs", failure_code="script_unsupported_reference",
        )
    notes: list[str] = []
    seen_urls: set[str] = set()
    for identifier in cited_ids:
        story = by_id[identifier]
        # Headlines are reporting data too; URLs embedded in a label are not citations.
        label = " ".join(minimize_editorial_text(re.sub(
            r"https?://\S+", "", story.headline, flags=re.IGNORECASE,
        )).split()) or "Newsletter story"
        public_urls = []
        for raw in story.source_urls:
            safe = normalize_url(raw)
            if safe and not is_tracking_url(safe) and safe not in public_urls:
                public_urls.append(safe)
        if not public_urls:
            notes.append(f"{label} — Newsletter source (no public link)")
        for url in public_urls:
            if url not in seen_urls:
                notes.append(f"{label} — {url}")
                seen_urls.add(url)
    quote_url = normalize_url(quote.source_url)
    if not quote_url or is_tracking_url(quote_url):
        raise DataValidationError(
            "closing quotation source must be a safe public URL",
            failure_code="source_evidence",
        )
    # Keep explicit quotation attribution even if a news story cites this same page.
    notes.append(f"Closing quotation — {quote.author} — {quote_url}")
    return notes


def _normalize_conversation_turns(data: dict[str, Any], maximum: int) -> dict[str, Any]:
    """Split only at existing sentence boundaries; preserve words, order and speakers.

    A long single sentence still requires model correction. Never cut, summarize,
    drop facts, change host attribution or apply this to broadcast/solo speech.
    Independent factual review remains mandatory after this formatting operation.
    """
    def split(raw):
        if not isinstance(raw, list):
            return raw
        turns = []
        for turn in raw:
            if not isinstance(turn, dict) or not isinstance(turn.get("text"), str):
                turns.append(turn)
                continue
            text = turn["text"]
            if len(text.split()) <= maximum:
                turns.append(turn)
                continue
            sentences = re.split(r"(?<=[.!?])\s+", text)
            if any(len(sentence.split()) > maximum for sentence in sentences):
                turns.append(turn)
                continue
            chunk = []
            for sentence in sentences:
                if chunk and len(" ".join([*chunk, sentence]).split()) > maximum:
                    turns.append({**turn, "text": " ".join(chunk)})
                    chunk = []
                chunk.append(sentence)
            if chunk:
                turns.append({**turn, "text": " ".join(chunk)})
        return turns

    result = dict(data)
    for key in ("introduction", "conclusion"):
        if key in result:
            result[key] = split(result[key])
    if isinstance(result.get("sections"), list):
        result["sections"] = [
            {**row, "dialogue": split(row["dialogue"])}
            if isinstance(row, dict) and "dialogue" in row else row
            for row in result["sections"]
        ]
    return result


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

