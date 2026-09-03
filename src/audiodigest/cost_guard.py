from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from audiodigest.config import Settings

FORBIDDEN_ENVIRONMENT_VARIABLES = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "VERTEX_AI_PROJECT",
)

FORBIDDEN_FIREBASE_KEYS = ("functions", "run", "apphosting", "storage")


class CostSafetyError(RuntimeError):
    """Raised when a configuration could result in an additional usage charge."""


@dataclass(frozen=True, slots=True)
class SparkConfirmation:
    project_id: str
    confirmed_at: str
    statement: str


def assert_no_paid_credentials(environment: dict[str, str] | None = None) -> None:
    environment = environment or dict(os.environ)
    present = [name for name in FORBIDDEN_ENVIRONMENT_VARIABLES if environment.get(name)]
    if present:
        names = ", ".join(present)
        raise CostSafetyError(
            f"Paid or metered credential environment variable(s) detected: {names}. "
            "Remove them from the launcher environment before running AudioDigest."
        )


def validate_firebase_json(path: Path) -> None:
    if not path.exists():
        raise CostSafetyError(f"Firebase configuration not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CostSafetyError(f"Could not parse {path}: {exc}") from exc
    hosting = config.get("hosting")
    if not isinstance(hosting, dict):
        raise CostSafetyError("firebase.json must contain static Hosting configuration")
    if any(key in config for key in FORBIDDEN_FIREBASE_KEYS):
        raise CostSafetyError(
            "firebase.json references a service that is unavailable on the no-cost Spark design"
        )
    rewrites = hosting.get("rewrites", [])
    if any(isinstance(item, dict) and ("function" in item or "run" in item) for item in rewrites):
        raise CostSafetyError("Firebase Hosting must not rewrite to Functions or Cloud Run")


def write_spark_confirmation(settings: Settings) -> SparkConfirmation:
    if not settings.firebase.project_id or "REPLACE_" in settings.firebase.project_id:
        raise CostSafetyError("Configure firebase.project_id before confirming Spark")
    confirmation = SparkConfirmation(
        project_id=settings.firebase.project_id,
        confirmed_at=datetime.now(UTC).isoformat(),
        statement=(
            "I verified in the Firebase console that this project is on Spark, "
            "has no linked Cloud Billing account, and must not be upgraded to Blaze."
        ),
    )
    settings.safety_confirmation_path.parent.mkdir(parents=True, exist_ok=True)
    settings.safety_confirmation_path.write_text(
        json.dumps(asdict(confirmation), indent=2), encoding="utf-8"
    )
    return confirmation


def validate_spark_confirmation(settings: Settings) -> None:
    if not settings.firebase.require_spark_confirmation:
        raise CostSafetyError(
            "Spark confirmation cannot be disabled in the zero-cost design"
        )
    try:
        raw = json.loads(settings.safety_confirmation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CostSafetyError(
            "Spark status has not been confirmed. Run 'audiodigest confirm-spark' "
            "after checking the Firebase console."
        ) from exc
    if raw.get("project_id") != settings.firebase.project_id:
        raise CostSafetyError("Spark confirmation belongs to a different Firebase project")
    if "must not be upgraded to Blaze" not in str(raw.get("statement", "")):
        raise CostSafetyError("Spark confirmation statement is invalid")


def run_cost_guard(settings: Settings, *, publishing: bool) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if os.environ.get("RUNNER_OS") != "Linux":
            raise CostSafetyError("the zero-cost cloud runner must use Linux")
        if os.environ.get("TDN_REPOSITORY_VISIBILITY") != "private":
            raise CostSafetyError(
                "production generation is disabled outside a private repository"
            )
        if os.environ.get("GITHUB_EVENT_NAME") not in {
            "schedule",
            "workflow_dispatch",
        }:
            raise CostSafetyError(
                "private cloud credentials are blocked for this workflow event"
            )
    if settings.safety.forbid_paid_credentials:
        assert_no_paid_credentials()
    validate_firebase_json(settings.project_dir / "firebase.json")
    if publishing:
        if not settings.firebase.publish_enabled:
            raise CostSafetyError("Publishing is disabled in config.toml")
        validate_spark_confirmation(settings)
