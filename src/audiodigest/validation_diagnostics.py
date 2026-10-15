"""Fixed-category diagnostic metadata. Never retain rejected values or source text."""

from __future__ import annotations

from typing import Any

FIELDS = {
    "cli_output", "cli_status", "cli_response", "cli_usage",
    "edits",
    "root",
    "stories",
    "story_id",
    "section",
    "headline",
    "facts",
    "why_it_matters",
    "source_ids",
    "source_urls",
    "evidence",
    "evidence_refs",
    "confidence",
    "rank_score",
    "title",
    "hosts",
    "introduction",
    "sections",
    "conclusion",
    "sign_off",
    "show_notes",
    "closing_comment",
    "disclosure",
    "dialogue",
    "host",
    "text",
    "approved",
    "issues",
    "factual_approved",
    "name",
    "story_ids",
    "narration",
}
VALUE_KINDS = {"missing", "null", "boolean", "string", "number", "object", "array", "other"}
RULES = {
    "cli_empty", "cli_object", "cli_error", "cli_unsuccessful", "cli_text_missing",
    "cli_exit", "cli_partial", "cli_duplicate_key", "cli_timeout", "cli_action_denied",
    "patch_shape", "patch_target", "patch_anchor", "patch_text", "patch_duplicate",
    "patch_turn_length",
    "boolean_required", "rejection_issues", "approval_consistent",
    "section_blank",
    "section_too_long",
    "section_slash",
    "section_control",
    "section_not_allowed",
    "finite_number",
    "probability_range",
    "json_syntax",
    "json_duplicate_key",
    "json_nonfinite",
    "nonempty",
    "object_shape",
    "host_count",
    "host_unique",
    "host_configured",
    "host_introduction",
    "host_participation",
    "editor_credit_once",
    "section_order",
    "section_unique",
    "section_first",
    "story_supported",
    "story_required",
    "story_section",
    "word_ceiling",
    "closing_shape",
    "immutable_repair",
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
    for key, allowed in (("field", FIELDS), ("value_kind", VALUE_KINDS), ("rule", RULES),
                         ("layer", {"cli", "model_json", "editorial"})):
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    for key in (
        "item_index",
        "fact_index",
        "section_index",
        "turn_index",
        "actual_count",
        "expected_count",
        "json_line",
        "json_column",
        "json_position",
        "attempt_ms",
        "stdout_bytes", "stderr_bytes", "exit_code", "exit_signal", "http_status",
    ):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**9:
            result[key] = value
    return result
