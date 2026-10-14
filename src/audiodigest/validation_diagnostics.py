"""Fixed-category diagnostic metadata. Never retain rejected values or source text."""

from __future__ import annotations

from typing import Any

FIELDS = {
    "root", "stories", "story_id", "section", "headline", "facts", "why_it_matters",
    "source_ids", "source_urls", "evidence", "evidence_refs", "confidence", "rank_score",
    "title", "hosts", "introduction", "sections", "conclusion", "sign_off", "show_notes",
    "closing_comment", "disclosure", "dialogue", "host", "text", "approved", "issues",
    "factual_approved",
}
VALUE_KINDS = {"missing", "null", "boolean", "string", "number", "object", "array", "other"}
RULES = {
    "section_blank", "section_too_long", "section_slash", "section_control", "section_not_allowed",
    "finite_number", "probability_range", "json_syntax", "json_duplicate_key", "json_nonfinite",
}
DIAGNOSTIC_LIMIT = 16


def value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "other"


def safe_detail(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, allowed in (("field", FIELDS), ("value_kind", VALUE_KINDS), ("rule", RULES)):
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    for key in ("item_index", "fact_index", "actual_count", "expected_count",
                "json_line", "json_column", "json_position"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**9:
            result[key] = value
    return result
