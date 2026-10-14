"""Local guidance for an already-budgeted draft retry; never a validation bypass."""

from __future__ import annotations

from typing import Any

from audiodigest.validation_diagnostics import safe_detail


def draft_repair_plan(
    payload: dict, response: dict | None, code: str, *, diagnostic: dict | None = None,
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "mode": "smallest-supported-correction",
        "validation_still_required": True,
        "preserve_valid_facts_attribution_and_qualifiers": True,
        "never_expand_to_reach_a_minimum": True,
    }
    detail = safe_detail(diagnostic)
    if detail:
        plan["constraint"] = detail
    if code.startswith("extraction_") or code in {
        "source_alias_unknown", "source_alias_unused", "source_url_unowned",
        "source_fact_alignment", "source_fact_support_shape", "source_passage_unknown",
    }:
        actions = {
            "source_alias_unused": (
                "Remove only source_ids aliases that support none of this story's facts. "
                "Keep all supported facts and their exact evidence_refs; do not drop news."
            ),
            "extraction_section": (
                "Use one exact configured desk; otherwise a valid concise auto desk."
            ),
            "extraction_section_limit": (
                "Consolidate related desks within the configured maximum; "
                "keep all supported stories."
            ),
            "extraction_confidence": (
                "Supply numeric confidence from 0 to 1, not a percentage or prose."
            ),
            "extraction_rank": (
                "Supply a finite numeric rank_score, never null, a boolean or prose."
            ),
            "extraction_duplicate_id": "Give every extracted story a unique local story_id.",
            "extraction_evidence_shape": (
                "Return evidence_refs only; the application rebuilds evidence support objects."
            ),
            "extraction_consolidation": (
                "Align each fact with exact supplied passage IDs; keep its original source support."
            ),
        }
        plan["action"] = (
            actions.get(code, (
                "Correct only source aliases, passage IDs, support alignment or supplied URLs."
            ))
            + " Preserve all valid reporting, quantities, attribution and qualifiers. "
            "Every fact needs one or two exact supplied passage IDs "
            "at its matching evidence_refs index. "
            "Never invent sources, URLs or support; empty source_urls is valid. "
            "Return the complete corrected extraction object, not a host script."
        )
        return plan
    if isinstance(payload.get("repair_response_contract"), dict):
        plan["action"] = (
            "Return a complete valid JSON response under repair_response_contract. "
            "Prefer bounded news-turn edits with exact expected text from previous_script, "
            "not the rejected edits. Correct all latest review issues using original "
            "source_evidence. Do not change speakers, story IDs, section order or closing "
            "through edits. For broader changes return a complete script under "
            "output_contract in this same response, never mixed with edits. "
            "Preserve already valid corrections; do not guess truncated content, "
            "add filler or weaken any structural or factual validator."
        )
        return plan
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
    elif code in {"script_coverage", "script_unsupported_reference", "script_section_coverage"}:
        plan["action"] = (
            "Explain missing required stories from original evidence in the proper section; "
            "do not merely attach IDs to unrelated prose. Preserve valid coverage. "
            "Remove unsupported IDs and any unsupported claims, not valid stories."
        )
        if code == "script_section_coverage":
            plan["action"] += (
                " Place each story and its actual reporting under its exact supplied desk; "
                "the same ID under an unrelated section does not count as coverage."
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
    elif code == "script_turn_length":
        plan["action"] = (
            "Rewrite only overlong conversation turns into complete shorter sentences, "
            "preserving each fact, qualifier, attribution and exact host. Follow "
            "output_contract.maximum_words_per_turn. Do not truncate or remove coverage."
        )
    elif code == "script_hosts":
        plan["action"] = (
            "Use output_contract.exact_hosts_in_order exactly. Introduce each active host, "
            "and, only for two-host conversation, let both speak in every multi-story section. "
            "Correct only "
            "the rejected host field or participation; any new narration must use original "
            "evidence. Do not rewrite valid news or change coverage, voices or attribution."
        )
        if detail.get("rule") == "host_introduction":
            plan["action"] = (
                "Correct only introduction: introduce each configured active host by exact name. "
                "Keep one editor/producer credit. Preserve all valid news dialogue and coverage."
            )
        elif detail.get("rule") == "host_participation" and "section_index" in detail:
            plan["action"] = (
                "Correct only the indicated conversation section so both configured hosts speak. "
                "Use distinct supported details, never filler or repeated facts. Keep every "
                "required story ID in that section and preserve other valid sections."
            )
    elif code == "script_editor_credit":
        plan["action"] = (
            "Keep exactly one editor/producer credit in the introduction, spoken by an "
            "active host. Dario Novelli is not a speaker. Preserve the remaining valid script."
        )
    elif code in {"script_section_name", "script_duplicate_section", "script_shape",
                  "model_required_field", "model_list_shape", "model_dialogue_shape"}:
        plan["action"] = (
            "Correct the extraction object, not a podcast script: stories is an array of "
            "objects with unique story_id, exact section, headline, facts, why_it_matters, "
            "source_ids, source_urls, evidence_refs, numeric confidence and rank_score. "
            "Use exact supplied source aliases and passage IDs. Keep facts and evidence_refs "
            "aligned. An empty source_urls array is valid. Never invent evidence."
        ) if isinstance(payload.get("sources"), list) else (
            "Follow output_contract and the JSON example exactly: required keys, objects, "
            "nonempty dialogue arrays of {host,text}, exact hosts and section names, "
            "one object per nonempty section, story_ids as arrays of supplied strings. "
            "Replace placeholder narration with supported reporting; never invent IDs. "
            "Preserve valid text and return show_notes as []; the application assembles references."
        ) if code.startswith("script_") or (
            isinstance(payload.get("output_contract"), dict)
            and "exact_hosts_in_order" in payload["output_contract"]
        ) else (
            "Fix the rejected field under the JSON schema in the original instruction. "
            "Preserve supported reporting and attribution. Do not convert a newspaper "
            "or verification object into host dialogue; use its own requested schema."
        )
    elif code == "script_reference_url":
        plan["action"] = (
            "Fix only show_notes: return []. The application assembles all references "
            "from cited source records and the approved quotation. Do not write URLs. "
            "Preserve supported narration and story coverage."
        )
    elif code == "source_evidence":
        plan["action"] = (
            "Use only exact source aliases, passage IDs and URLs supplied in original "
            "evidence. Every facts entry needs aligned evidence_refs from its cited source. "
            "Never manufacture or repair a missing passage or URL by guessing. "
            "A newsletter with no public URL remains usable evidence; omit invented URLs."
        )
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
