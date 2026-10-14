"""Bounded news-turn replacements; anchors are not factual verification."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from audiodigest.models import DataValidationError

MAX_SCRIPT_EDITS = 12
MAX_EDIT_CHARACTERS = 10_000


def script_repair_contract(maximum_words_per_turn: int | None) -> dict[str, Any]:
    return {
        "preferred_response_schema": {
            "type": "object", "required": ["edits"], "additionalProperties": False,
            "properties": {"edits": {
                "type": "array", "minItems": 1, "maxItems": MAX_SCRIPT_EDITS,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["section_index", "turn_index", "expected", "replacement"],
                    "properties": {
                        "section_index": {"type": "integer", "minimum": 0,
                                          "description": "Index in previous_script.sections"},
                        "turn_index": {"type": "integer", "minimum": 0,
                                       "description": "Index in that section's dialogue"},
                        "expected": {"type": "string", "minLength": 1,
                                     "maxLength": MAX_EDIT_CHARACTERS,
                                     "description": "Copy the exact current turn text"},
                        "replacement": {"type": "string", "minLength": 1,
                                        "maxLength": MAX_EDIT_CHARACTERS,
                                        "description": "Complete corrected source-supported copy"},
                    },
                },
            }},
        },
        "maximum_edits": MAX_SCRIPT_EDITS,
        "maximum_characters_per_text": MAX_EDIT_CHARACTERS,
        "maximum_words_per_replacement": maximum_words_per_turn,
        "immutable": "all fields except existing news-turn text",
        "broader_repair": "return a complete script under output_contract instead of edits",
        "independent_full_factual_review_required": True,
    }


def apply_script_edits(
    original: dict[str, Any], proposal: dict[str, Any],
    *, maximum_words_per_turn: int | None = None,
) -> dict[str, Any]:
    """Apply only exact, unique replacements to a copy; reject an entire bad batch.

    Never infer targets from free-form reviewer prose, evaluate replacement text,
    change source lineage or use a text match as proof of a claim. The caller must
    run the full script validators and independent original-source factual review.
    Error messages are fixed: no private anchors, indexes, IDs or draft snippets.
    """
    def reject(rule: str = "patch_shape", **detail) -> None:
        raise DataValidationError(
            "script repair must use bounded exact news-turn replacements",
            failure_code="script_shape", diagnostic={"field": "edits", "rule": rule, **detail},
        )

    if not isinstance(proposal, dict) or set(proposal) != {"edits"}:
        reject()
    edits = proposal["edits"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_SCRIPT_EDITS:
        reject()
    result = deepcopy(original)
    seen: set[tuple[int, int]] = set()
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict) or set(edit) != {
            "section_index", "turn_index", "expected", "replacement",
        }:
            reject(item_index=index)
        section, turn = edit["section_index"], edit["turn_index"]
        if type(section) is not int or type(turn) is not int or section < 0 or turn < 0:
            reject("patch_target", item_index=index)
        if (section, turn) in seen:
            reject("patch_duplicate", item_index=index, section_index=section, turn_index=turn)
        seen.add((section, turn))
        expected, replacement = edit["expected"], edit["replacement"]
        if any(not isinstance(text, str) or not text.strip()
               or len(text) > MAX_EDIT_CHARACTERS for text in (expected, replacement)):
            reject("patch_text", item_index=index, section_index=section, turn_index=turn)
        if replacement != replacement.strip() or replacement == expected:
            reject("patch_text", item_index=index, section_index=section, turn_index=turn)
        if maximum_words_per_turn is not None and len(replacement.split()) > maximum_words_per_turn:
            reject("patch_turn_length", item_index=index, section_index=section, turn_index=turn,
                   actual_count=len(replacement.split()), expected_count=maximum_words_per_turn)
        try:
            target = result["sections"][section]["dialogue"][turn]
            if not isinstance(target, dict) or target.get("text") != expected:
                reject("patch_anchor", item_index=index, section_index=section, turn_index=turn)
            target["text"] = replacement
        except (IndexError, KeyError, TypeError):
            reject("patch_target", item_index=index, section_index=section, turn_index=turn)
    return result
