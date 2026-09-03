from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass

from audiodigest.antigravity_client import (
    AntigravityCLIError,
    enforce_safe_antigravity_settings,
)
from audiodigest.config import Settings
from audiodigest.cost_guard import (
    CostSafetyError,
    assert_no_paid_credentials,
    validate_firebase_json,
)
from audiodigest.runtime_environment import local_tool_environment


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _binary_check(
    name: str,
    executable: str,
    args: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> Check:
    resolved = shutil.which(executable)
    if not resolved:
        return Check(name, False, f"not found: {executable}")
    try:
        completed = subprocess.run(
            [resolved, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return Check(name, False, str(exc))
    output = (completed.stdout or completed.stderr).strip().splitlines()
    detail = output[0][:300] if output else f"exit {completed.returncode}"
    return Check(name, completed.returncode == 0, detail)


def run_doctor(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    environment = local_tool_environment(
        dict(os.environ),
        settings.app.runtime_dir,
    )
    try:
        assert_no_paid_credentials()
    except CostSafetyError as exc:
        checks.append(Check("No paid credentials", False, str(exc)))
    else:
        checks.append(Check("No paid credentials", True, "none detected"))

    try:
        validate_firebase_json(settings.project_dir / "firebase.json")
    except CostSafetyError as exc:
        checks.append(Check("Static Firebase configuration", False, str(exc)))
    else:
        checks.append(Check("Static Firebase configuration", True, "Hosting only"))

    try:
        enforce_safe_antigravity_settings(settings.antigravity)
    except AntigravityCLIError as exc:
        checks.append(Check("Antigravity privacy and cost settings", False, str(exc)))
    else:
        checks.append(
            Check(
                "Antigravity privacy and cost settings",
                True,
                "useG1Credits=false; enableTelemetry=false",
            )
        )

    firebase_check = _binary_check(
        "Firebase CLI",
        settings.firebase.executable,
        ["--version"],
        environment=environment,
    )
    if not settings.firebase.publish_enabled and not firebase_check.ok:
        firebase_check = Check(
            "Firebase CLI",
            True,
            "optional while private-feed publishing is disabled",
        )
    checks.extend(
        [
            _binary_check(
                "Antigravity CLI",
                settings.antigravity.executable,
                ["--version"],
                environment=environment,
            ),
            firebase_check,
            _binary_check(
                "FFmpeg",
                settings.audio.ffmpeg,
                ["-version"],
                environment=environment,
            ),
            _binary_check(
                "FFprobe",
                settings.audio.ffprobe,
                ["-version"],
                environment=environment,
            ),
        ]
    )
    checks.append(
        Check(
            "Gmail OAuth client",
            settings.gmail.client_secret_path.is_file(),
            str(settings.gmail.client_secret_path),
        )
    )
    checks.append(
        Check(
            "Antigravity read-only agent",
            settings.antigravity.agent_path.is_file(),
            str(settings.antigravity.agent_path),
        )
    )
    spark_confirmed = settings.safety_confirmation_path.is_file()
    checks.append(
        Check(
            "Spark confirmation",
            spark_confirmed or not settings.firebase.publish_enabled,
            (
                str(settings.safety_confirmation_path)
                if spark_confirmed
                else "optional while private-feed publishing is disabled"
            ),
        )
    )
    return checks


def checks_as_json(checks: list[Check]) -> str:
    return json.dumps([asdict(item) for item in checks], indent=2)
