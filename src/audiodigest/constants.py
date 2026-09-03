from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum


class Section(StrEnum):
    TODAY_IN_HISTORY = "TIH: Today in History"
    AI = "AI"
    DATA = "Data"
    CLOUD_SOFTWARE = "Cloud and Software"
    CYBERSECURITY_IT = "Cybersecurity and IT"
    TECH_BUSINESS_PRODUCTS = "Tech Business and Products"
    WORLD_POLITICS_NEWS = "World Politics and News"
    NATIONAL_POLITICS_NEWS = "National Politics and News"
    LOCAL_REGIONAL_NEWS = "Local and Regional News"
    BUSINESS_ECONOMY = "Business and Economy"
    SPORTS = "Sports"
    GOOD_NEWS_CULTURE = "Good News and Culture"


SECTION_ORDER: tuple[Section, ...] = tuple(Section)
DEFAULT_SECTION_NAMES: tuple[str, ...] = tuple(item.value for item in SECTION_ORDER)
MAX_PODCAST_SECTIONS = 10


class SectionLabel(str):
    """A validated custom section label with Enum-compatible serialization."""

    @property
    def value(self) -> str:
        return str(self)


SectionReference = Section | SectionLabel


def validate_section_name(value: str) -> str:
    name = " ".join(value.split()).strip()
    if not name:
        raise ValueError("podcast section names must not be blank")
    if len(name) > 60:
        raise ValueError("podcast section names must be 60 characters or fewer")
    if "/" in name:
        raise ValueError("podcast section names must use 'and' instead of '/'")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ValueError("podcast section names must not contain control characters")
    return name


def normalize_custom_sections(
    values: Sequence[str],
    *,
    include_today_in_history: bool = True,
) -> tuple[str, ...]:
    """Normalize user-selected sections, optionally reserving TIH as the opener.

    Users may select up to ten subject sections.  TIH is a separate optional
    editorial feature and therefore never consumes one of those ten choices.
    """

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = validate_section_name(value)
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"duplicate podcast section: {name}")
        seen.add(folded)
        normalized.append(name)
    tih = Section.TODAY_IN_HISTORY.value
    selected = tuple(name for name in normalized if name.casefold() != tih.casefold())
    if len(selected) > MAX_PODCAST_SECTIONS:
        raise ValueError(
            f"configure no more than {MAX_PODCAST_SECTIONS} podcast sections"
        )
    if not selected:
        # Empty means content-derived sections. Research availability decides
        # whether auto mode receives a TIH opener.
        return ()
    return ((tih,) if include_today_in_history else ()) + selected


def parse_section(
    value: str,
    *,
    allowed_sections: Sequence[str] | None = DEFAULT_SECTION_NAMES,
) -> SectionReference:
    name = validate_section_name(value)
    if allowed_sections is not None and name not in allowed_sections:
        allowed = ", ".join(allowed_sections)
        raise ValueError(f"section must be one of: {allowed}")
    try:
        return Section(name)
    except ValueError:
        return SectionLabel(name)

AI_DISCLOSURE = (
    "This is an AI-generated summary of selected newsletters and public research sources. "
    "Please verify important information using the sources in the show notes."
)
