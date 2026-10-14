"""Optional extraction diagnostics, never evidence or a publication gate.

Only transient source aliases, fixed categories and numbers enter the ledger.
Model explanations are explicitly unverified; absent or conflicting explanations
remain unknown. No email text or identifiers are copied into diagnostics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from audiodigest.models import SourceItem, Story

MODEL_REASONS = {
    "no_substantive_news", "promotional", "outside_sections", "insufficient_evidence",
}
REASON_COUNTERS = {
    "no_supplied_text": "newsletter_no_supplied_text",
    **{reason: f"newsletter_model_{reason}" for reason in sorted(MODEL_REASONS)},
    "unknown": "newsletter_reason_unknown",
}
CAP_COUNTERS = {"newsletter_body_input_capped", "newsletter_article_input_capped"}
ACCOUNTING_COUNTERS = set(REASON_COUNTERS.values()) | CAP_COUNTERS


def newsletter_accounting(
    sources: list[SourceItem],
    prompts: list[dict[str, Any]],
    stories: list[Story],
    reported: Any = None,
    *,
    configured_sections: bool = False,
) -> list[dict[str, Any]]:
    """Account for validated, consolidated news; never trust model coverage claims."""
    represented = {
        source_id for story in stories if story.section.value != "TIH: Today in History"
        for source_id in story.source_ids
    }
    aliases = {
        prompt["message_id"] for source, prompt in zip(sources, prompts, strict=True)
        if source.source_type == "newsletter"
    }
    reasons: dict[str, str] = {}
    seen: set[str] = set()
    if isinstance(reported, list) and len(reported) <= min(len(aliases), 1000):
        for item in reported:
            if not isinstance(item, dict):
                continue
            alias = item.get("source_id")
            if not isinstance(alias, str) or alias not in aliases:
                continue
            if alias in seen:
                reasons[alias] = "unknown"
                continue
            seen.add(alias)
            reason = item.get("reason")
            if (
                set(item) == {"source_id", "reason"}
                and isinstance(reason, str) and reason in MODEL_REASONS
                and (reason != "outside_sections" or configured_sections)
            ):
                reasons[alias] = reason
    ledger = []
    for source, prompt in zip(sources, prompts, strict=True):
        if source.source_type != "newsletter":
            continue
        body_count = len(prompt.get("newsletter_passages", []))
        article_count = sum(len(article.get("passages", [])) for article in prompt["articles"])
        retained = source.message_id in represented
        reason, basis = "retained_news", "observed"
        if not retained:
            if body_count + article_count == 0:
                reason = "no_supplied_text"
            else:
                reason = reasons.get(prompt["message_id"], "unknown")
                basis = "model_reported" if reason in MODEL_REASONS else "unknown"
        ledger.append({
            "source_alias": prompt["message_id"],
            "retained_news": retained,
            "newsletter_passages": body_count,
            "article_passages": article_count,
            "body_input_capped": len(source.email_text) > 50_000,
            "article_input_capped": any(len(article.text) > 50_000 for article in source.articles),
            "reason": reason,
            "reason_basis": basis,
        })
    return ledger


def accounting_counts(ledger: list[dict[str, Any]]) -> dict[str, int]:
    """Summarize application-built rows only, never a raw model response."""
    result = dict.fromkeys(ACCOUNTING_COUNTERS, 0)
    for row in ledger:
        if not row["retained_news"]:
            result[REASON_COUNTERS[row["reason"]]] += 1
        result["newsletter_body_input_capped"] += int(row["body_input_capped"])
        result["newsletter_article_input_capped"] += int(row["article_input_capped"])
    return result
