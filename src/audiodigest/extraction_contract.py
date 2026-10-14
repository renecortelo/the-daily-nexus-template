"""Repair only unambiguous generated metadata; facts and source references stay exact."""

from __future__ import annotations

import math
import re
from typing import Any


def normalize_extraction(
    data: dict[str, Any], sections: tuple[str, ...] | None,
) -> tuple[dict, int]:
    raw = data.get("stories")
    if not isinstance(raw, list):
        return data, 0
    # Identity is local metadata. Keep valid IDs; assign a fresh unique ID only
    # to a duplicate, without merging, dropping or altering its supported news.
    reserved = {row["story_id"].strip() for row in raw if isinstance(row, dict)
                and isinstance(row.get("story_id"), str)}
    seen, records, repaired = set(), [], 0
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            records.append(row)
            continue
        item = dict(row)
        if "evidence" in item:
            # Support objects are rebuilt from actual source passages by bind().
            # The model supplies evidence_refs, never trusted support objects.
            item.pop("evidence")
            repaired += 1
        identifier = item.get("story_id")
        if isinstance(identifier, str) and identifier.strip():
            identifier = identifier.strip()
            if identifier in seen:
                replacement = f"extracted-{index + 1:04d}"
                while replacement in reserved:
                    replacement += "-r"
                item["story_id"] = replacement
                reserved.add(replacement)
                identifier = replacement
                repaired += 1
            seen.add(identifier)
        section = item.get("section")
        if isinstance(section, str) and sections:
            normalized = " ".join(section.replace("&", " and ").split()).casefold()
            matches = [name for name in sections if
                       " ".join(name.replace("&", " and ").split()).casefold() == normalized]
            if len(matches) == 1 and section != matches[0]:
                item["section"] = matches[0]
                repaired += 1
        for key in ("confidence", "rank_score"):
            value = item.get(key)
            if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip()):
                number = float(value)
                if math.isfinite(number) and (key != "confidence" or 0 <= number <= 1):
                    item[key] = number
                    repaired += 1
        records.append(item)
    return {**data, "stories": records}, repaired
