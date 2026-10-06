"""Conservative, local-only preparation of English speech, not editorial rewriting.

Keep the verified script and displayed transcript untouched. Only exact technical
terms are respelled here; amounts, dates, quotations and unknown names are not
guessed or translated. Misaki handles ordinary numbers and punctuation itself.
"""

from __future__ import annotations

import re
import unicodedata

# Misaki's phoneme notation, rather than English respellings that can themselves
# be out of vocabulary. Exact matching protects ordinary words and product names.
_PRONUNCIATIONS = {
    "AI": "ˌAˈI",
    "API": "ˌApˌiˈI",
    "APIs": "ˌApˌiˈIz",
    "CPU": "sˌipˌijˈu",
    "CPUs": "sˌipˌijˈuz",
    "ETL": "ˌitˌiˈɛl",
    "GPU": "ʤˌipˌijˈu",
    "GPUs": "ʤˌipˌijˈuz",
    "GPT": "ʤˌipˌitˈi",
    "IT": "ˌItˈi",
    "Kokoro": "kˈOkəɹO",
    "LLM": "ˌɛlˌɛlˈɛm",
    "LLMs": "ˌɛlˌɛlˈɛmz",
    "PDF": "pˌidˌiˈɛf",
    "PDFs": "pˌidˌiˈɛfs",
    "RSS": "ˌɑɹˌɛsˈɛs",
    "SQL": "ˌɛskjˌuˈɛl",
}
_VERSIONS = re.compile(
    r"\b(?P<term>GPT|Python|Kokoro|Antigravity|Node(?:\.js)?|FFmpeg|version|v)"
    r"(?P<separator>[ \t]*-?[ \t]*)(?P<version>\d+(?:\.\d+){1,3})(?!\d|\.\d)"
)


def validate_pronunciations(values: object) -> dict[str, str]:
    """Accept a small local phoneme dictionary, never arbitrary markup or URLs."""

    if not isinstance(values, dict) or len(values) > 40:
        raise ValueError("audio.pronunciations must be a table with up to 40 terms")
    for term, phonemes in values.items():
        if (
            not isinstance(term, str)
            or not 1 <= len(term) <= 60
            or not all(char.isalnum() or char in " .-'" for char in term)
            or not term.strip()
        ):
            raise ValueError("audio.pronunciations terms must be short names without markup")
        if (
            not isinstance(phonemes, str)
            or not 1 <= len(phonemes) <= 100
            or not all(
                char == " " or (ord(char) <= 0x036F and unicodedata.category(char)[0] in {"L", "M"})
                for char in phonemes
            )
            or not phonemes.strip()
        ):
            raise ValueError("audio.pronunciations values must contain phonemes, not directions")
    return dict(values)


def prepare_speech(
    text: str, *, is_heading: bool = False, pronunciations: dict[str, str] | None = None
) -> str:
    """Return a synthesis-only form, without changing the source script."""

    if is_heading and text.strip() == "TIH: Today in History":
        return "Today in History."

    def speak_version(match: re.Match[str]) -> str:
        term = "version" if match["term"] == "v" else match["term"]
        return term + " " + match["version"].replace(".", " point ")

    spoken = _VERSIONS.sub(speak_version, text)
    dictionary = {
        **_PRONUNCIATIONS,
        **validate_pronunciations({} if pronunciations is None else pronunciations),
    }
    terms = re.compile(
        r"\[[^\]\n]+\]\(/[^/\n]+/\)|(?<!\w)(?:"
        + "|".join(re.escape(term) for term in sorted(dictionary, key=len, reverse=True))
        + r")(?!\w)"
    )
    return terms.sub(
        lambda match: (
            match.group()
            if match.group().startswith("[")
            else f"[{match.group()}](/{dictionary[match.group()]}/)"
        ),
        spoken,
    )


def boundary_pause_ms(
    *,
    is_heading: bool,
    next_is_heading: bool,
    phase_changed: bool,
    host_changed: bool,
    ends_with_question: bool,
) -> int:
    """Desired total quiet gap, before subtracting the model's own silence."""

    if next_is_heading:
        return 600
    if is_heading:
        return 250
    if phase_changed:
        return 420
    if ends_with_question:
        return 160
    if host_changed:
        return 200
    return 300
