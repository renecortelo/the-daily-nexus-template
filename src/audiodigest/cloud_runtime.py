from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from audiodigest.cost_guard import SparkConfirmation
from audiodigest.private_store import (
    PrivateStoreError,
    delete_private_value,
    write_private_value,
)


class CloudRuntimeError(RuntimeError):
    """Raised before any private cloud credential is used."""


SECRET_ENVIRONMENT = (
    "TDN_GMAIL_TOKEN_JSON",
    "TDN_FIREBASE_REFRESH_TOKEN",
    "TDN_ANTIGRAVITY_KEYRING_JSON",
    "TDN_FIREBASE_DEPLOY_TOKEN",
    "TDN_FIREBASE_PROJECT_ID",
    "TDN_FIREBASE_API_KEY",
    "TDN_FIREBASE_OWNER_UID",
    "TDN_FIREBASE_SECRET_PATH",
    "TDN_SPARK_CONFIRMED",
)
ALLOWED_EVENTS = frozenset({"schedule", "workflow_dispatch"})
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
FIREBASE_KEY_PATTERN = re.compile(r"^AIza[0-9A-Za-z_-]{30,50}$")
PRIVATE_PATH_PATTERN = re.compile(r"^[0-9A-Za-z_-]{32,128}$")
SPARK_ACKNOWLEDGEMENT = "SPARK_NO_BILLING_CONFIRMED"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise CloudRuntimeError(f"required encrypted secret is unavailable: {name}")
    return value


def _json_secret(name: str) -> dict:
    raw = _required_environment(name)
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CloudRuntimeError(f"{name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise CloudRuntimeError(f"{name} must contain a JSON object")
    return value


def _validate_github_boundary() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise CloudRuntimeError("private cloud materialization is restricted to GitHub Actions")
    if os.environ.get("RUNNER_OS") != "Linux":
        raise CloudRuntimeError("the zero-cost private cloud runner must use Linux")
    if os.environ.get("TDN_REPOSITORY_VISIBILITY") != "private":
        raise CloudRuntimeError("production generation is disabled outside a private repository")
    if os.environ.get("GITHUB_EVENT_NAME") not in ALLOWED_EVENTS:
        raise CloudRuntimeError("private credentials are blocked for this workflow event")


def _validate_probe_credentials() -> dict[str, str]:
    project_id = _required_environment("TDN_FIREBASE_PROJECT_ID").strip()
    api_key = _required_environment("TDN_FIREBASE_API_KEY").strip()
    owner_uid = _required_environment("TDN_FIREBASE_OWNER_UID").strip()
    firebase_refresh = _required_environment("TDN_FIREBASE_REFRESH_TOKEN").strip()
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise CloudRuntimeError("Firebase project ID has an invalid format")
    if not FIREBASE_KEY_PATTERN.fullmatch(api_key):
        raise CloudRuntimeError("Firebase Web API key has an invalid format")
    if (
        not owner_uid
        or len(owner_uid) > 128
        or "/" in owner_uid
        or any(character.isspace() for character in owner_uid)
    ):
        raise CloudRuntimeError("Firebase owner UID has an invalid format")
    if len(firebase_refresh) < 20:
        raise CloudRuntimeError("Firebase authorization secret is unexpectedly short")
    if _required_environment("TDN_SPARK_CONFIRMED") != SPARK_ACKNOWLEDGEMENT:
        raise CloudRuntimeError("the no-billing Firebase Spark acknowledgement is missing")
    return {
        "project_id": project_id,
        "api_key": api_key,
        "owner_uid": owner_uid,
        "firebase_refresh": firebase_refresh,
    }


def _validate_generation_credentials() -> dict[str, str]:
    secret_path = _required_environment("TDN_FIREBASE_SECRET_PATH").strip()
    firebase_deploy = _required_environment("TDN_FIREBASE_DEPLOY_TOKEN").strip()
    if not PRIVATE_PATH_PATTERN.fullmatch(secret_path):
        raise CloudRuntimeError("private feed path must contain at least 128 bits")
    if len(firebase_deploy) < 20:
        raise CloudRuntimeError("Firebase authorization secret is unexpectedly short")

    gmail = _json_secret("TDN_GMAIL_TOKEN_JSON")
    required_gmail = {"refresh_token", "client_id", "client_secret", "token_uri"}
    if not required_gmail.issubset(gmail):
        raise CloudRuntimeError("Gmail token JSON is missing OAuth refresh fields")
    if gmail.get("token_uri") != "https://oauth2.googleapis.com/token":
        raise CloudRuntimeError("Gmail token JSON contains an unexpected token endpoint")
    scopes = gmail.get("scopes", [])
    if scopes and (
        not isinstance(scopes, list)
        or "https://www.googleapis.com/auth/gmail.readonly" not in scopes
        or any(
            isinstance(scope, str)
            and scope.startswith("https://www.googleapis.com/auth/gmail.")
            and scope != "https://www.googleapis.com/auth/gmail.readonly"
            for scope in scopes
        )
    ):
        raise CloudRuntimeError("Gmail token must remain limited to gmail.readonly")

    antigravity = _json_secret("TDN_ANTIGRAVITY_KEYRING_JSON")
    antigravity_token = antigravity.get("token")
    if (
        not isinstance(antigravity.get("auth_method"), str)
        or not antigravity["auth_method"]
        or not isinstance(antigravity.get("id_token"), str)
        or len(antigravity["id_token"]) < 100
        or not isinstance(antigravity_token, dict)
    ):
        raise CloudRuntimeError("Antigravity keyring JSON has an invalid structure")
    for field in ("access_token", "refresh_token", "token_type", "expiry"):
        value = antigravity_token.get(field)
        if not isinstance(value, str) or not value:
            raise CloudRuntimeError(
                f"Antigravity keyring JSON is missing token field: {field}"
            )
    if len(antigravity_token["refresh_token"]) < 20:
        raise CloudRuntimeError(
            "Antigravity keyring refresh token is unexpectedly short"
        )
    return {
        "secret_path": secret_path,
        "firebase_deploy": firebase_deploy,
        "gmail_json": json.dumps(gmail, separators=(",", ":")),
        "antigravity_keyring_json": json.dumps(
            antigravity,
            separators=(",", ":"),
        ),
    }


def _replace_template(template: str, replacements: dict[str, str]) -> str:
    result = template
    for marker, value in replacements.items():
        if result.count(marker) != 1:
            raise CloudRuntimeError(
                f"cloud configuration marker is missing or duplicated: {marker}"
            )
        result = result.replace(marker, json.dumps(value))
    if "__TDN_" in result:
        raise CloudRuntimeError("cloud configuration contains an unresolved marker")
    return result


def prepare_cloud_runtime(
    *,
    template_path: Path,
    output_path: Path,
    phase: str = "run",
) -> Path:
    _validate_github_boundary()
    if phase not in {"probe", "run"}:
        raise CloudRuntimeError("cloud runtime phase must be probe or run")
    values = _validate_probe_credentials()
    if phase == "run":
        values.update(_validate_generation_credentials())
    runner_temp = Path(_required_environment("RUNNER_TEMP")).resolve()
    github_workspace = Path(_required_environment("GITHUB_WORKSPACE")).resolve()
    if output_path.resolve().parent != github_workspace:
        raise CloudRuntimeError("generated cloud configuration must stay at repository root")
    runtime = runner_temp / "the-daily-nexus"
    secrets_root = runtime / "secrets"
    gmail_token_path = secrets_root / "gmail-token.json"
    firebase_refresh_path = secrets_root / "firebase-refresh-token"
    firebase_deploy_path = secrets_root / "firebase-deploy-token"
    antigravity_keyring_path = secrets_root / "antigravity-keyring.json"
    gmail_client_placeholder = secrets_root / "interactive-gmail-client-disabled.json"
    web_oauth_placeholder = secrets_root / "interactive-web-client-disabled.json"
    antigravity_root = Path.home() / ".gemini"
    antigravity_settings_path = (
        antigravity_root / "antigravity-cli" / "settings.json"
    )
    global_antigravity_settings_path = antigravity_root / "settings.json"

    try:
        write_private_value(firebase_refresh_path, values["firebase_refresh"])
        if phase == "run":
            write_private_value(gmail_token_path, values["gmail_json"])
            write_private_value(firebase_deploy_path, values["firebase_deploy"])
            write_private_value(
                antigravity_keyring_path,
                values["antigravity_keyring_json"],
            )
            write_private_value(
                antigravity_settings_path,
                json.dumps(
                    {
                        "enableTelemetry": False,
                        "useG1Credits": False,
                        "trustedWorkspaces": [str(runtime / "antigravity-workspace")],
                        "permissions": {
                            "allow": [f"read_file({runtime / 'antigravity-workspace'})"]
                        },
                    },
                    indent=2,
                ),
            )
            write_private_value(
                global_antigravity_settings_path,
                json.dumps(
                    {"security": {"auth": {"selectedType": "oauth-personal"}}},
                    indent=2,
                ),
            )
        confirmation = SparkConfirmation(
            project_id=values["project_id"],
            confirmed_at=datetime.now(UTC).isoformat(),
            statement=(
                "I verified in the Firebase console that this project is on Spark, "
                "has no linked Cloud Billing account, and must not be upgraded to Blaze."
            ),
        )
        write_private_value(
            runtime / "spark-confirmation.json",
            json.dumps(asdict(confirmation), indent=2),
        )
        template = template_path.read_text(encoding="utf-8")
        rendered = _replace_template(
            template,
            {
                "__TDN_RUNTIME_DIR__": str(runtime),
                "__TDN_GMAIL_CLIENT_PATH__": str(gmail_client_placeholder),
                "__TDN_GMAIL_TOKEN_PATH__": str(gmail_token_path),
                "__TDN_ANTIGRAVITY_WORKSPACE__": str(
                    runtime / "antigravity-workspace"
                ),
                "__TDN_ANTIGRAVITY_SETTINGS__": str(antigravity_settings_path),
                "__TDN_FIREBASE_PROJECT_ID__": values["project_id"],
                "__TDN_FIREBASE_BASE_URL__": (
                    f"https://{values['project_id']}.web.app"
                ),
                "__TDN_FIREBASE_SECRET_PATH__": values.get(
                    "secret_path",
                    "0" * 32,
                ),
                "__TDN_FIREBASE_DEPLOY_TOKEN_PATH__": str(firebase_deploy_path),
                "__TDN_FIREBASE_API_KEY__": values["api_key"],
                "__TDN_FIREBASE_OWNER_UID__": values["owner_uid"],
                "__TDN_WEB_OAUTH_PLACEHOLDER__": str(web_oauth_placeholder),
                "__TDN_FIREBASE_REFRESH_TOKEN_PATH__": str(firebase_refresh_path),
            },
        )
        write_private_value(output_path, rendered)
    except (OSError, PrivateStoreError) as exc:
        raise CloudRuntimeError("could not materialize the private cloud runtime") from exc
    return output_path


def cleanup_cloud_runtime(*, config_path: Path) -> None:
    runner_temp = os.environ.get("RUNNER_TEMP", "")
    if runner_temp:
        runtime = Path(runner_temp).resolve() / "the-daily-nexus"
        if runtime.parent == Path(runner_temp).resolve() and runtime.name == "the-daily-nexus":
            shutil.rmtree(runtime, ignore_errors=True)
    for path in (
        config_path,
        Path.home() / ".gemini" / "settings.json",
        Path.home() / ".gemini" / "antigravity-cli" / "settings.json",
    ):
        try:
            delete_private_value(path)
        except PrivateStoreError:
            pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="audiodigest-cloud-runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--template", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--phase", choices=("probe", "run"), default="run")
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare_cloud_runtime(
            template_path=args.template.resolve(),
            output_path=args.output.resolve(),
            phase=args.phase,
        )
        print(
            f"Private cloud {args.phase} runtime prepared without displaying credentials."
        )
        return
    cleanup_cloud_runtime(config_path=args.config.resolve())
    print("Private cloud runtime credentials removed.")


if __name__ == "__main__":
    main()
