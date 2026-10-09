"""Bounded, local-only measurement of the delivered MP3, not a second encode."""

from __future__ import annotations

import math
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from audiodigest.execution_budget import operation_timeout

AUDIT_SECONDS = 30
PUBLISH_RESERVE_SECONDS = 120
BALANCED_ENCODING_PROFILE = "balanced_speech_v1"


@dataclass(frozen=True, slots=True)
class AudioQuality:
    status: str = "unavailable"
    reason: str = "not_measured"
    integrated_lufs: float | None = None
    true_peak_db: float | None = None
    loudness_range_lu: float | None = None
    analysis_seconds: float | None = None
    encoding_profile: str | None = None

    def to_dict(self) -> dict[str, str | float]:
        # Never persist the command, filenames, decoder diagnostics or source text.
        result: dict[str, str | float] = {"status": self.status, "reason": self.reason}
        if self.encoding_profile == BALANCED_ENCODING_PROFILE:
            result["encoding_profile"] = self.encoding_profile
        for name in (
            "integrated_lufs", "true_peak_db", "loudness_range_lu", "analysis_seconds"
        ):
            value = getattr(self, name)
            if value is not None and math.isfinite(value):
                result[name] = value
        return result


def _parse_summary(stderr: str) -> tuple[float, float, float] | None:
    # Match only final ebur128 fields; partial/NaN/silent output is not a measurement.
    summary = stderr.rsplit("Summary:", 1)[-1] if "Summary:" in stderr else ""
    patterns = (
        r"Integrated loudness:\s+I:\s+(-?\d+(?:\.\d+)?) LUFS",
        r"True peak:\s+Peak:\s+(-?\d+(?:\.\d+)?) dBFS",
        r"Loudness range:\s+LRA:\s+(\d+(?:\.\d+)?) LU",
    )
    matches = [re.search(pattern, summary) for pattern in patterns]
    if not all(matches):
        return None
    values = tuple(float(match.group(1)) for match in matches if match)
    loudness, peak, loudness_range = values
    if not (-70 < loudness <= 5 and -100 <= peak <= 10 and 0 <= loudness_range <= 100):
        return None
    return loudness, peak, loudness_range


def measure_encoded_audio(
    ffmpeg: str, path: Path, *, target_lufs: float, true_peak_db: float
) -> AudioQuality:
    """Observe encoded audio; an unavailable audit never restarts synthesis.

    Reserve two minutes for publishing, skip near the protected deadline and cap
    the decoder at 30 seconds. Do not catch a global execution-budget exception.
    """
    if operation_timeout(AUDIT_SECONDS + PUBLISH_RESERVE_SECONDS) < (
        AUDIT_SECONDS + PUBLISH_RESERVE_SECONDS
    ):
        return AudioQuality(reason="publication_reserve")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info",
                "-i", str(path), "-af", "ebur128=peak=true:framelog=verbose",
                "-f", "null", "-",
            ],
            timeout=operation_timeout(AUDIT_SECONDS),
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return AudioQuality(reason="timeout", analysis_seconds=time.monotonic() - started)
    except (OSError, UnicodeError):
        return AudioQuality(
            reason="decoder_unavailable", analysis_seconds=time.monotonic() - started
        )
    elapsed = time.monotonic() - started
    values = _parse_summary(completed.stderr) if completed.returncode == 0 else None
    if values is None:
        return AudioQuality(reason="invalid_measurement", analysis_seconds=elapsed)
    loudness, peak, loudness_range = values
    within_target = abs(loudness - target_lufs) <= 1 and peak <= true_peak_db
    return AudioQuality(
        status="within_target" if within_target else "outside_target",
        reason="measured",
        integrated_lufs=loudness,
        true_peak_db=peak,
        loudness_range_lu=loudness_range,
        analysis_seconds=elapsed,
    )
