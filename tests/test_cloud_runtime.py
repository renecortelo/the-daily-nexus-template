import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.cloud_runtime import (
    CloudRuntimeError,
    cleanup_cloud_runtime,
    prepare_cloud_runtime,
)
from audiodigest.config import load_settings


class CloudRuntimeTests(TestCase):
    def _environment(self, root: Path) -> dict[str, str]:
        gmail = {
            "refresh_token": "gmail-refresh-token-value",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }
        antigravity = {
            "auth_method": "personal",
            "id_token": "i" * 200,
            "token": {
                "refresh_token": "antigravity-refresh-token-value",
                "access_token": "expired-access-token",
                "token_type": "Bearer",
                "expiry": "2026-07-31T12:00:00Z",
            },
        }
        return {
            "GITHUB_ACTIONS": "true",
            "RUNNER_OS": "Linux",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_WORKSPACE": str(root),
            "RUNNER_TEMP": str(root / "runner-temp"),
            "TDN_REPOSITORY_VISIBILITY": "private",
            "TDN_GMAIL_TOKEN_JSON": json.dumps(gmail),
            "TDN_FIREBASE_REFRESH_TOKEN": "firebase-refresh-token-value",
            "TDN_ANTIGRAVITY_KEYRING_JSON": json.dumps(antigravity),
            "TDN_FIREBASE_DEPLOY_TOKEN": "firebase-deployment-token-value",
            "TDN_FIREBASE_PROJECT_ID": "example-private-project",
            "TDN_FIREBASE_API_KEY": "AI" + "za" + ("x" * 35),
            "TDN_FIREBASE_OWNER_UID": "owner-uid",
            "TDN_FIREBASE_SECRET_PATH": "a" * 32,
            "TDN_SPARK_CONFIRMED": "SPARK_NO_BILLING_CONFIRMED",
        }

    def test_private_runtime_is_materialized_with_safety_flags(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "config.toml.cloud"
            home = root / "home"
            environment = dict(os.environ)
            environment.update(self._environment(root))
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("audiodigest.cloud_runtime.Path.home", return_value=home),
            ):
                prepare_cloud_runtime(
                    template_path=Path("config.cloud.example.toml").resolve(),
                    output_path=output,
                )
                settings = load_settings(output)
                self.assertFalse(settings.antigravity.use_g1_credits)
                self.assertFalse(settings.antigravity.telemetry)
                self.assertTrue(settings.firebase.publish_enabled)
                self.assertIsNotNone(settings.gmail.token_file_path)
                self.assertIsNotNone(settings.web.token_file_path)
                cleanup_cloud_runtime(config_path=output)
                self.assertFalse(output.exists())

    def test_probe_phase_materializes_only_the_firebase_runner_token(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "config.toml.cloud"
            home = root / "home"
            environment = dict(os.environ)
            environment.update(self._environment(root))
            for secret_name in (
                "TDN_GMAIL_TOKEN_JSON",
                "TDN_ANTIGRAVITY_KEYRING_JSON",
                "TDN_FIREBASE_DEPLOY_TOKEN",
                "TDN_FIREBASE_SECRET_PATH",
            ):
                environment.pop(secret_name)
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("audiodigest.cloud_runtime.Path.home", return_value=home),
            ):
                prepare_cloud_runtime(
                    template_path=Path("config.cloud.example.toml").resolve(),
                    output_path=output,
                    phase="probe",
                )
                settings = load_settings(output)
                self.assertTrue(settings.web.token_file_path.is_file())
                self.assertFalse(settings.gmail.token_file_path.is_file())
                self.assertFalse(
                    settings.firebase.deployment_token_file_path.is_file()
                )
                self.assertFalse((home / ".gemini" / "oauth_creds.json").exists())

    def test_public_repository_is_refused_before_materialization(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            environment = dict(os.environ)
            environment.update(self._environment(root))
            environment["TDN_REPOSITORY_VISIBILITY"] = "public"
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(CloudRuntimeError, "private repository"):
                    prepare_cloud_runtime(
                        template_path=Path("config.cloud.example.toml").resolve(),
                        output_path=root / "config.toml.cloud",
                    )
