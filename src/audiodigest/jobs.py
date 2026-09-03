from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from audiodigest.config import Settings, validate_settings
from audiodigest.constants import normalize_custom_sections
from audiodigest.preferences import (
    controls_for_voice_id,
    editorial_tone,
    validate_gmail_label,
    voice_id_for_controls,
    voice_profile,
)


class JobValidationError(ValueError):
    pass


WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_SCHEDULE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


def _required_string(data: dict[str, Any], key: str, *, maximum: int = 120) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobValidationError(f"{key} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise JobValidationError(f"{key} must be {maximum} characters or fewer")
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise JobValidationError(f"{key} must not contain control characters")
    return result


def _clock(value: Any, key: str) -> time:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise JobValidationError(f"{key} must use HH:MM")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise JobValidationError(f"{key} must use HH:MM") from exc


@dataclass(frozen=True, slots=True)
class GenerationParameters:
    run_name: str
    gmail_label: str
    sections: tuple[str, ...]
    host_count: int
    solo_name: str
    dialogue_style: str
    primary_voice: str
    primary_tone: str
    secondary_voice: str
    secondary_tone: str
    publish: bool
    date_mode: str = "previous_day"
    include_today_in_history: bool = True
    newspaper_edition_scale: str = "standard"
    evidence_mode: str = "newsletter_first"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationParameters:
        if not isinstance(data, dict):
            raise JobValidationError("parameters must be an object")
        raw_sections = data.get("sections", [])
        if not isinstance(raw_sections, list) or any(
            not isinstance(item, str) for item in raw_sections
        ):
            raise JobValidationError("sections must be a list of strings")
        try:
            run_name = _required_string(
                {"runName": data.get("runName") or "Daily Nexus"},
                "runName",
                maximum=80,
            )
            include_today_in_history = data.get("includeTih", True)
            if not isinstance(include_today_in_history, bool):
                raise JobValidationError("includeTih must be true or false")
            newspaper_edition_scale = str(
                data.get("editionScale") or "standard"
            ).strip().casefold()
            if newspaper_edition_scale not in {
                "focused",
                "standard",
                "comprehensive",
            }:
                raise JobValidationError(
                    "editionScale must be focused, standard, or comprehensive"
                )
            evidence_mode = str(
                data.get("evidenceMode") or "newsletter_first"
            ).strip().casefold()
            if evidence_mode not in {"newsletter_first", "newsletter_only"}:
                raise JobValidationError(
                    "evidenceMode must be newsletter_first or newsletter_only"
                )
            if evidence_mode == "newsletter_only" and include_today_in_history:
                raise JobValidationError(
                    "newsletter_only mode requires TIH to be turned off"
                )
            sections = normalize_custom_sections(
                tuple(raw_sections),
                include_today_in_history=include_today_in_history,
            )
            gmail_label = validate_gmail_label(
                _required_string(data, "gmailLabel", maximum=225)
            )
            primary_voice_profile = voice_profile(
                _required_string(data, "primaryVoice")
            )
            primary_voice = primary_voice_profile.voice_id
            primary_tone = editorial_tone(
                _required_string(data, "primaryTone")
            ).tone_id
            secondary_voice_profile = voice_profile(
                str(data.get("secondaryVoice") or "am_michael")
            )
            secondary_voice = secondary_voice_profile.voice_id
            secondary_tone = editorial_tone(
                str(data.get("secondaryTone") or "dry_wit")
            ).tone_id
        except ValueError as exc:
            raise JobValidationError(str(exc)) from exc
        host_count = data.get("hostCount")
        if host_count not in {1, 2}:
            raise JobValidationError("hostCount must be 1 or 2")
        solo_name = str(data.get("soloName") or "Dalia")
        if solo_name not in {"Dalia", "Nox"}:
            raise JobValidationError("soloName must be Dalia or Nox")
        primary_host = "Dalia" if host_count == 2 else solo_name
        expected_gender = "Female" if primary_host == "Dalia" else "Male"
        if primary_voice_profile.gender != expected_gender:
            # Older schedules were allowed to retain a hidden voice after a
            # host-format change. Preserve the selected delivery personality
            # while choosing the host-appropriate local counterpart.
            _, personality = controls_for_voice_id(primary_voice)
            primary_voice = voice_id_for_controls(expected_gender, personality)
            primary_voice_profile = voice_profile(primary_voice)
        if host_count == 2:
            if secondary_voice_profile.gender != "Male":
                _, personality = controls_for_voice_id(secondary_voice)
                secondary_voice = voice_id_for_controls("Male", personality)
                secondary_voice_profile = voice_profile(secondary_voice)
            if secondary_voice == primary_voice:
                raise JobValidationError("Dalia and Nox must use distinct local voices")
        dialogue_style = str(data.get("dialogueStyle") or "broadcast")
        if dialogue_style not in {"broadcast", "conversation"}:
            raise JobValidationError(
                "dialogueStyle must be broadcast or conversation"
            )
        publish = data.get("publish")
        if not isinstance(publish, bool):
            raise JobValidationError("publish must be true or false")
        date_mode = str(data.get("dateMode") or "previous_day")
        if date_mode not in {"previous_day", "today"}:
            raise JobValidationError("dateMode must be previous_day or today")
        return cls(
            run_name=run_name,
            gmail_label=gmail_label,
            sections=sections,
            host_count=host_count,
            solo_name=solo_name,
            dialogue_style=dialogue_style,
            primary_voice=primary_voice,
            primary_tone=primary_tone,
            secondary_voice=secondary_voice,
            secondary_tone=secondary_tone,
            publish=publish,
            date_mode=date_mode,
            include_today_in_history=include_today_in_history,
            newspaper_edition_scale=newspaper_edition_scale,
            evidence_mode=evidence_mode,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runName": self.run_name,
            "gmailLabel": self.gmail_label,
            "sections": list(self.sections),
            "hostCount": self.host_count,
            "soloName": self.solo_name,
            "dialogueStyle": self.dialogue_style,
            "primaryVoice": self.primary_voice,
            "primaryTone": self.primary_tone,
            "secondaryVoice": self.secondary_voice,
            "secondaryTone": self.secondary_tone,
            "publish": self.publish,
            "dateMode": self.date_mode,
            "includeTih": self.include_today_in_history,
            "editionScale": self.newspaper_edition_scale,
            "evidenceMode": self.evidence_mode,
        }


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    schedule_id: str
    name: str
    enabled: bool
    timezone: str
    start_time: time
    ready_by: time
    weekdays: tuple[int, ...]
    parameters: GenerationParameters

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        schedule_id: str | None = None,
    ) -> ScheduledJob:
        if not isinstance(data, dict):
            raise JobValidationError("schedule must be an object")
        identifier = schedule_id or _required_string(
            data,
            "scheduleId",
            maximum=40,
        )
        if not _SCHEDULE_ID.fullmatch(identifier):
            raise JobValidationError(
                "scheduleId must contain lowercase letters, numbers, or hyphens"
            )
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise JobValidationError("enabled must be true or false")
        timezone = _required_string(data, "timezone", maximum=80)
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise JobValidationError(f"unknown timezone: {timezone}") from exc
        raw_weekdays = data.get("weekdays")
        if (
            not isinstance(raw_weekdays, list)
            or not raw_weekdays
            or any(
                not isinstance(item, int)
                or isinstance(item, bool)
                or item not in range(7)
                for item in raw_weekdays
            )
        ):
            raise JobValidationError(
                "weekdays must contain weekday numbers from 0 (Monday) to 6 (Sunday)"
            )
        weekdays = tuple(sorted(set(raw_weekdays)))
        start_time = _clock(data.get("startTime"), "startTime")
        ready_by = _clock(data.get("readyBy"), "readyBy")
        if start_time >= ready_by:
            raise JobValidationError("startTime must be earlier than readyBy")
        return cls(
            schedule_id=identifier,
            name=_required_string(data, "name"),
            enabled=enabled,
            timezone=timezone,
            start_time=start_time,
            ready_by=ready_by,
            weekdays=weekdays,
            parameters=GenerationParameters.from_dict(data.get("parameters", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheduleId": self.schedule_id,
            "name": self.name,
            "enabled": self.enabled,
            "timezone": self.timezone,
            "startTime": self.start_time.strftime("%H:%M"),
            "readyBy": self.ready_by.strftime("%H:%M"),
            "weekdays": list(self.weekdays),
            "parameters": self.parameters.to_dict(),
            "schemaVersion": 1,
        }

    def local_now(self, now: datetime) -> datetime:
        if now.tzinfo is None:
            raise JobValidationError("scheduler time must include a timezone")
        return now.astimezone(ZoneInfo(self.timezone))

    def is_due(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        local = self.local_now(now)
        if local.weekday() not in self.weekdays:
            return False
        start = datetime.combine(local.date(), self.start_time, local.tzinfo)
        # A delayed cloud trigger catches up later the same day. The remote
        # execution claim prevents a successful or failed task from rerunning.
        return local >= start

    def episode_date(self, now: datetime) -> date:
        local_day = self.local_now(now).date()
        if self.parameters.date_mode == "previous_day":
            return local_day - timedelta(days=1)
        return local_day


def apply_generation_parameters(
    settings: Settings,
    parameters: GenerationParameters,
) -> Settings:
    configured = copy.deepcopy(settings)
    configured.app.gmail_label = parameters.gmail_label
    configured.podcast.sections = parameters.sections
    configured.podcast.include_today_in_history = parameters.include_today_in_history
    configured.podcast.newspaper_edition_scale = parameters.newspaper_edition_scale
    configured.podcast.evidence_mode = parameters.evidence_mode
    configured.hosts.count = parameters.host_count
    configured.hosts.solo_name = parameters.solo_name
    configured.hosts.dialogue_style = parameters.dialogue_style
    configured.hosts.primary_voice = parameters.primary_voice
    configured.hosts.primary_tone = parameters.primary_tone
    configured.hosts.secondary_voice = parameters.secondary_voice
    configured.hosts.secondary_tone = parameters.secondary_tone
    if parameters.host_count == 1 and parameters.solo_name == "Nox":
        # The form's primary controls describe the only speaking host. Keep the
        # configured Nox profile aligned even though he is the secondary named host.
        configured.hosts.secondary_voice = parameters.primary_voice
        configured.hosts.secondary_tone = parameters.primary_tone
    configured.audio.voice = parameters.primary_voice
    configured.audio.language_code = voice_profile(
        parameters.primary_voice
    ).language_code
    configured.podcast.tone = parameters.primary_tone
    configured.firebase.publish_mode = (
        "automatic" if parameters.publish else "manual"
    )
    validate_settings(configured)
    return configured
