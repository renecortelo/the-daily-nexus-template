"""Bounded owner-only measurements, not provider billing or account quotas."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from audiodigest.progress import safe_event

SAMPLE_LIMIT = 20
RESOURCE_FIELDS = {
    "staged_bytes",
    "retained_audio_bytes",
    "retained_audio_count",
    "new_audio_bytes",
    "new_paper_bytes",
    "new_preview_bytes",
    "retention_episodes",
}
TIMING_FIELDS = {
    "start_delay_seconds",
    "setup_seconds",
    "job_observed_seconds",
    "ready_by_late_seconds",
    "protected_remaining_seconds",
}


def numeric_fields(raw: Any, fields: set[str]) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: round(float(value), 3)
        for key, value in raw.items()
        if key in fields
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and 0 <= value <= 10**12
        and math.isfinite(value)
    }


def timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value.astimezone(UTC) if isinstance(value, datetime) and value.tzinfo else None


def safe_sample(raw: Any) -> dict | None:
    if not isinstance(raw, dict) or timestamp(raw.get("at")) is None:
        return None
    profile = safe_event(raw)
    if (profile is None or profile["event"] != "finish"
            or profile["status"] not in {"completed", "failed", "interrupted"}):
        return None
    profile["measurements"] = numeric_fields(raw.get("measurements"), TIMING_FIELDS)
    profile["resources"] = numeric_fields(raw.get("resources"), RESOURCE_FIELDS)
    for key in ("expected_start_at", "ready_by_at", "generation_started_at"):
        if parsed := timestamp(raw.get(key)):
            profile[key] = parsed.isoformat()
    if isinstance(raw.get("origin"), str) and raw["origin"] in {"manual", "scheduled"}:
        profile["origin"] = raw["origin"]
    return profile


def measured_profile(
    profile: dict,
    *,
    started_at: datetime,
    expected_start: datetime | None = None,
    ready_by: datetime | None = None,
    resources: dict | None = None,
    environment: dict[str, str] | None = None,
) -> dict:
    """Enrich only finished allowlisted profiles; absent measurements stay absent."""
    result = safe_sample(profile)
    if result is None:
        raise ValueError("invalid terminal timing profile")
    start, end = timestamp(started_at), timestamp(profile.get("at"))
    expected, target = timestamp(expected_start), timestamp(ready_by)
    result["resources"] = numeric_fields(resources, RESOURCE_FIELDS)
    values = result["measurements"]
    if start:
        result["generation_started_at"] = start.isoformat()
        if expected and start >= expected:
            result["expected_start_at"] = expected.isoformat()
            values["start_delay_seconds"] = (start - expected).total_seconds()
    if target and end:
        result["origin"] = "scheduled"
        result["ready_by_at"] = target.isoformat()
        values["ready_by_late_seconds"] = max(0, (end - target).total_seconds())
    elif expected:
        result["origin"] = "manual"
    env = environment or {}
    if env.get("GITHUB_ACTIONS") == "true":
        for env_key, field, reference in (
            ("TDN_RUNNER_STARTED_EPOCH", "setup_seconds", start),
            ("TDN_RUNNER_STARTED_EPOCH", "job_observed_seconds", end),
            ("TDN_RUNNER_DEADLINE_EPOCH", "protected_remaining_seconds", end),
        ):
            try:
                epoch = float(env.get(env_key, ""))
                elapsed = (
                    (
                        epoch - reference.timestamp()
                        if field == "protected_remaining_seconds"
                        else reference.timestamp() - epoch
                    )
                    if reference
                    else -1
                )
                if math.isfinite(epoch) and 0 <= elapsed <= 3600:
                    values[field] = round(elapsed, 3)
            except (ValueError, OverflowError):
                pass
    result["measurements"] = numeric_fields(values, TIMING_FIELDS)
    return result


def append_sample(previous: Any, latest: dict) -> dict:
    """No task IDs/names/URLs in retained samples; one bounded owner document."""
    prior = previous.get("recent", []) if isinstance(previous, dict) else []
    if not isinstance(prior, list):
        prior = []
    samples = [sample for raw in prior[-SAMPLE_LIMIT:] if (sample := safe_sample(raw))]
    if not samples and (sample := safe_sample(previous)):
        samples = [sample]
    # Repeat synchronization must not duplicate the same terminal measurement.
    samples = [sample for sample in samples if sample["at"] != latest["at"]]
    return {**latest, "recent": [*samples, latest][-SAMPLE_LIMIT:]}
