from __future__ import annotations

import re
import secrets
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from audiodigest.config import load_settings
from audiodigest.cost_guard import (
    validate_firebase_json,
    validate_spark_confirmation,
)
from audiodigest.preferences import (
    PreferenceValidationError,
    _set_toml_string,
    _set_toml_value,
)

PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


@dataclass(frozen=True, slots=True)
class PublishingSetupResult:
    project_id: str
    base_url: str
    created_new_secret: bool


def _write_validated_config(config_path: Path, value: str) -> None:
    try:
        tomllib.loads(value)
    except tomllib.TOMLDecodeError as exc:
        raise PreferenceValidationError(
            f"The publishing setup would make config.toml invalid: {exc}"
        ) from exc
    temporary = config_path.with_suffix(".toml.tmp")
    temporary.write_text(value, encoding="utf-8")
    try:
        for attempt in range(4):
            try:
                temporary.replace(config_path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def configure_private_publishing(
    config_path: Path,
    project_id: str,
) -> PublishingSetupResult:
    normalized = project_id.strip().lower()
    if not PROJECT_ID_PATTERN.fullmatch(normalized):
        raise PreferenceValidationError(
            "Firebase project ID must be 6-30 lowercase letters, numbers, or "
            "hyphens; it must start with a letter and end with a letter or number."
        )
    original = config_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(original)
    firebase = parsed.get("firebase")
    existing_secret = (
        str(firebase.get("secret_path", "")).strip()
        if isinstance(firebase, dict)
        else ""
    )
    secret = existing_secret if len(existing_secret) >= 32 else secrets.token_hex(16)
    base_url = f"https://{normalized}.web.app"
    updated = _set_toml_string(original, "firebase", "project_id", normalized)
    updated = _set_toml_string(updated, "firebase", "base_url", base_url)
    updated = _set_toml_string(updated, "firebase", "secret_path", secret)
    updated = _set_toml_value(
        updated,
        "firebase",
        "require_spark_confirmation",
        "true",
    )
    updated = _set_toml_value(updated, "firebase", "publish_enabled", "false")
    updated = _set_toml_string(updated, "firebase", "publish_mode", "manual")
    _write_validated_config(config_path, updated)
    return PublishingSetupResult(
        project_id=normalized,
        base_url=base_url,
        created_new_secret=secret != existing_secret,
    )


def enable_private_publishing(config_path: Path) -> None:
    settings = load_settings(config_path)
    validate_firebase_json(settings.project_dir / "firebase.json")
    validate_spark_confirmation(settings)
    original = config_path.read_text(encoding="utf-8")
    updated = _set_toml_value(
        original,
        "firebase",
        "publish_enabled",
        "true",
    )
    validation_path = config_path.with_suffix(".publishing-validation.toml")
    try:
        validation_path.write_text(updated, encoding="utf-8")
        load_settings(validation_path)
    finally:
        validation_path.unlink(missing_ok=True)
    _write_validated_config(config_path, updated)


def disable_private_publishing(config_path: Path) -> None:
    original = config_path.read_text(encoding="utf-8")
    updated = _set_toml_value(
        original,
        "firebase",
        "publish_enabled",
        "false",
    )
    _write_validated_config(config_path, updated)
