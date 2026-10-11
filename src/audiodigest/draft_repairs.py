"""Local guidance for an already-budgeted draft retry; never a validation bypass."""

from __future__ import annotations

from typing import Any


def draft_repair_plan(payload: dict, response: dict | None, code: str) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "mode": "smallest-supported-correction",
        "validation_still_required": True,
        "preserve_valid_facts_attribution_and_qualifiers": True,
        "never_expand_to_reach_a_minimum": True,
    }
    if code in {"json_invalid", "model_incomplete"}:
        plan["action"] = (
            "Return a complete valid JSON object using supplied evidence. Do not guess "
            "missing text or treat a truncated response as verified content."
        )
        return plan
    if code == "script_length":
        budget = payload.get("episode_budget")
        ceiling = budget.get("max_words") if isinstance(budget, dict) else None
        if isinstance(ceiling, int) and not isinstance(ceiling, bool) and 0 < ceiling <= 10000:
            plan["maximum_words"] = ceiling
        plan["action"] = (
            "Compress redundant wording in existing supported coverage; do not add words. "
            "Keep required story IDs, material facts, attribution and qualifiers. "
            "Respect the supplied upper limit; never cut a sentence mid-thought."
        )
    elif code in {"script_coverage", "script_unsupported_reference"}:
        plan["action"] = (
            "Explain missing required stories from original evidence in the proper section; "
            "do not merely attach IDs to unrelated prose. Preserve valid coverage. "
            "Remove unsupported IDs and any unsupported claims, not valid stories."
        )
        contract = payload.get("output_contract")
        sections = contract.get("sections", []) if isinstance(contract, dict) else []
        required = payload.get("required_story_ids", [])
        required = {item for item in required if isinstance(item, str)} \
            if isinstance(required, list) else set()
        cited = set()
        raw_sections = response.get("sections", []) if isinstance(response, dict) else []
        if isinstance(raw_sections, list):
            for section in raw_sections:
                ids = section.get("story_ids", []) if isinstance(section, dict) else []
                if isinstance(ids, list):
                    cited.update(item for item in ids if isinstance(item, str))
        if isinstance(sections, list):
            plan["missing_required_by_section"] = [
                {"name": section["name"], "story_ids": [
                    item for item in section.get("required_story_ids", [])
                    if isinstance(item, str) and item in required and item not in cited
                ]}
                for section in sections
                if isinstance(section, dict) and isinstance(section.get("name"), str)
                and isinstance(section.get("required_story_ids", []), list)
            ]
            plan["missing_required_by_section"] = [
                row for row in plan["missing_required_by_section"] if row["story_ids"]
            ]
    elif code == "script_closing_comment":
        plan["action"] = (
            "Fix only closing_comment: zero or one object with an exact active host and "
            "nonempty short text. An empty list is allowed when no useful safe joke exists. "
            "The application inserts the quotation, author and source; do not rewrite them. "
            "Do not rewrite valid introduction or news sections for this optional comment."
        )
    else:
        plan["action"] = (
            "Fix the rejected fields under the supplied contract, preserving otherwise "
            "supported material. Respect upper word limits; never add filler or restore "
            "superseded unsupported wording."
        )
    return plan
