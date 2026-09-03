from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


class PreferenceValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    voice_id: str
    display_name: str
    gender: str
    description: str
    language_code: str

    @property
    def label(self) -> str:
        return f"{self.display_name} | {self.gender} | {self.description}"


@dataclass(frozen=True, slots=True)
class EditorialTone:
    tone_id: str
    display_name: str
    description: str
    prompt_instruction: str

    @property
    def label(self) -> str:
        return f"{self.display_name} | {self.description}"


VOICE_PROFILES = (
    VoiceProfile("am_michael", "Michael", "Male", "warm broadcast", "a"),
    VoiceProfile("am_eric", "Eric", "Male", "clear and analytical", "a"),
    VoiceProfile("am_puck", "Puck", "Male", "lively and energetic", "a"),
    VoiceProfile("af_heart", "Heart", "Female", "warm and engaging", "a"),
    VoiceProfile("af_bella", "Bella", "Female", "polished and expressive", "a"),
    VoiceProfile("bf_emma", "Emma", "Female", "formal British", "b"),
)

EDITORIAL_TONES = (
    EditorialTone(
        "neutral",
        "Neutral",
        "balanced and analytical",
        (
            "Use a neutral, balanced, analytical broadcast style. Be clear and engaging, "
            "but avoid sarcasm and overt jokes."
        ),
    ),
    EditorialTone(
        "dry_wit",
        "Dry wit",
        "smart with restrained sarcasm",
        (
            "Use intelligent dry wit and occasional restrained sarcasm. Keep humor subtle, "
            "evidence-safe, and secondary to clarity."
        ),
    ),
    EditorialTone(
        "fun",
        "Fun",
        "lively and energetic",
        (
            "Use a lively, upbeat, playful broadcast style. Add light humor and momentum "
            "without exaggerating facts or trivializing serious events."
        ),
    ),
    EditorialTone(
        "warm",
        "Warm",
        "friendly and conversational",
        (
            "Use a warm, approachable, conversational style with curiosity and empathy. "
            "Prefer gentle humor over sarcasm."
        ),
    ),
    EditorialTone(
        "formal",
        "Very formal",
        "precise and authoritative",
        (
            "Use a highly formal, precise, authoritative news style. Keep humor minimal and "
            "avoid slang, sarcasm, and casual asides."
        ),
    ),
)

VOICE_BY_ID = {item.voice_id: item for item in VOICE_PROFILES}
VOICE_BY_LABEL = {item.label: item for item in VOICE_PROFILES}
GENDER_CHOICES = ("Female", "Male")
PERSONALITY_CHOICES = (
    "Warm and engaging",
    "Clear and analytical",
    "Polished and expressive",
)
VOICE_SELECTIONS = {
    ("Female", "Warm and engaging"): "af_heart",
    ("Female", "Clear and analytical"): "bf_emma",
    ("Female", "Polished and expressive"): "af_bella",
    ("Male", "Warm and engaging"): "am_michael",
    ("Male", "Clear and analytical"): "am_eric",
    ("Male", "Polished and expressive"): "am_puck",
}
VOICE_CONTROLS_BY_ID = {
    voice_id: controls for controls, voice_id in VOICE_SELECTIONS.items()
}
TONE_BY_ID = {item.tone_id: item for item in EDITORIAL_TONES}
TONE_BY_LABEL = {item.label: item for item in EDITORIAL_TONES}
PUBLISHING_MODE_LABELS = {
    "Manual - review before publishing": "manual",
    "Automatic - publish after generation": "automatic",
}
PUBLISHING_MODE_BY_ID = {mode_id: label for label, mode_id in PUBLISHING_MODE_LABELS.items()}
DIALOGUE_STYLE_LABELS = {
    "Broadcast - structured news handoffs": "broadcast",
    "Conversation - natural reactions and discussion": "conversation",
}
DIALOGUE_STYLE_BY_ID = {
    style_id: label for label, style_id in DIALOGUE_STYLE_LABELS.items()
}


def validate_gmail_label(value: str) -> str:
    label = value.strip()
    if not label:
        raise PreferenceValidationError("Enter the exact Gmail label name.")
    if len(label) > 225:
        raise PreferenceValidationError("The Gmail label name must be 225 characters or fewer.")
    if any(ord(character) < 32 or ord(character) == 127 for character in label):
        raise PreferenceValidationError("The Gmail label cannot contain control characters.")
    if label.startswith("/") or label.endswith("/") or "//" in label:
        raise PreferenceValidationError(
            "Use '/' only between Gmail label levels, for example AudioDigest/Source."
        )
    return label


def voice_profile(voice_id: str) -> VoiceProfile:
    try:
        return VOICE_BY_ID[voice_id]
    except KeyError as exc:
        allowed = ", ".join(item.voice_id for item in VOICE_PROFILES)
        raise PreferenceValidationError(
            f"Unsupported narrator voice {voice_id!r}. Choose one of: {allowed}."
        ) from exc


def voice_id_for_controls(gender: str, personality: str) -> str:
    try:
        return VOICE_SELECTIONS[(gender, personality)]
    except KeyError as exc:
        raise PreferenceValidationError(
            "Choose Female or Male and one of the three delivery personalities."
        ) from exc


def controls_for_voice_id(voice_id: str) -> tuple[str, str]:
    try:
        return VOICE_CONTROLS_BY_ID[voice_id]
    except KeyError as exc:
        raise PreferenceValidationError(
            f"The configured local voice {voice_id!r} is not available in this interface."
        ) from exc


def editorial_tone(tone_id: str) -> EditorialTone:
    try:
        return TONE_BY_ID[tone_id]
    except KeyError as exc:
        allowed = ", ".join(item.tone_id for item in EDITORIAL_TONES)
        raise PreferenceValidationError(
            f"Unsupported editorial tone {tone_id!r}. Choose one of: {allowed}."
        ) from exc


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _set_toml_value(
    source: str,
    section: str,
    key: str,
    rendered_value: str,
) -> str:
    lines = source.splitlines(keepends=True)
    section_pattern = re.compile(rf"^\s*\[{re.escape(section)}]\s*(?:#.*)?$")
    key_pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=.*$")
    section_start = next(
        (index for index, line in enumerate(lines) if section_pattern.match(line.rstrip("\r\n"))),
        None,
    )
    rendered = f"{key} = {rendered_value}\n"
    if section_start is None:
        separator = "" if not source or source.endswith(("\n", "\r")) else "\n"
        return f"{source}{separator}\n[{section}]\n{rendered}"

    section_end = next(
        (
            index
            for index in range(section_start + 1, len(lines))
            if re.match(r"^\s*\[", lines[index])
        ),
        len(lines),
    )
    for index in range(section_start + 1, section_end):
        match = key_pattern.match(lines[index].rstrip("\r\n"))
        if match:
            newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
            lines[index] = f"{match.group(1)}{key} = {rendered_value}{newline}"
            return "".join(lines)
    lines.insert(section_end, rendered)
    return "".join(lines)


def _set_toml_string(source: str, section: str, key: str, value: str) -> str:
    return _set_toml_value(source, section, key, _toml_string(value))


def save_preferences(
    config_path: Path,
    *,
    gmail_label: str,
    voice_id: str | None = None,
    tone_id: str | None = None,
    host_count: int = 1,
    solo_name: str = "Dalia",
    dialogue_style: str = "broadcast",
    primary_voice_id: str | None = None,
    primary_tone_id: str | None = None,
    secondary_voice_id: str = "am_michael",
    secondary_tone_id: str = "dry_wit",
    publishing_mode: str = "manual",
) -> None:
    label = validate_gmail_label(gmail_label)
    if host_count not in {1, 2}:
        raise PreferenceValidationError("Choose either one host or two hosts.")
    if solo_name not in {"Dalia", "Nox"}:
        raise PreferenceValidationError("The solo host must be Dalia or Nox.")
    if dialogue_style not in {"broadcast", "conversation"}:
        raise PreferenceValidationError(
            "The two-host format must be broadcast or conversation."
        )
    primary_voice = voice_profile(primary_voice_id or voice_id or "af_heart")
    primary_tone = editorial_tone(primary_tone_id or tone_id or "warm")
    secondary_voice = voice_profile(secondary_voice_id)
    secondary_tone = editorial_tone(secondary_tone_id)
    if publishing_mode not in {"manual", "automatic"}:
        raise PreferenceValidationError("Publishing mode must be manual or automatic.")
    original = config_path.read_text(encoding="utf-8")
    updated = _set_toml_string(original, "app", "gmail_label", label)
    updated = _set_toml_string(updated, "audio", "voice", primary_voice.voice_id)
    updated = _set_toml_string(
        updated,
        "audio",
        "language_code",
        primary_voice.language_code,
    )
    updated = _set_toml_string(updated, "podcast", "tone", primary_tone.tone_id)
    updated = _set_toml_value(updated, "hosts", "count", str(host_count))
    updated = _set_toml_string(updated, "hosts", "solo_name", solo_name)
    updated = _set_toml_string(
        updated,
        "hosts",
        "dialogue_style",
        dialogue_style,
    )
    updated = _set_toml_string(
        updated,
        "hosts",
        "primary_voice",
        primary_voice.voice_id,
    )
    updated = _set_toml_string(
        updated,
        "hosts",
        "primary_tone",
        primary_tone.tone_id,
    )
    updated = _set_toml_string(
        updated,
        "hosts",
        "secondary_voice",
        secondary_voice.voice_id,
    )
    updated = _set_toml_string(
        updated,
        "hosts",
        "secondary_tone",
        secondary_tone.tone_id,
    )
    updated = _set_toml_string(
        updated,
        "firebase",
        "publish_mode",
        publishing_mode,
    )
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise PreferenceValidationError(
            f"The preferences would make config.toml invalid: {exc}"
        ) from exc
    temporary = config_path.with_suffix(".toml.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(config_path)
