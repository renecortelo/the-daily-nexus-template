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
    def test_cleanup_outside_private_actions_never_removes_local_files(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / "config.toml.cloud"
            config.write_text("untouched")
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}, clear=True):
                with self.assertRaises(CloudRuntimeError):
                    cleanup_cloud_runtime(config_path=config)
            self.assertEqual("untouched", config.read_text())

    def test_cleanup_rejects_wrong_config_and_broad_temporary_root_before_deleting(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            sentinel = root / "keep.toml"
            sentinel.write_text("untouched")
            environment = dict(os.environ)
            environment.update(self._environment(root))
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(CloudRuntimeError, "config.toml.cloud"):
                    cleanup_cloud_runtime(config_path=sentinel)
            environment["RUNNER_TEMP"] = str(root)
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(CloudRuntimeError, "workspace, home or root"):
                    cleanup_cloud_runtime(config_path=root / "config.toml.cloud")
            self.assertEqual("untouched", sentinel.read_text())

    def test_cleanup_rejects_a_linked_runtime_without_touching_its_target(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            environment = dict(os.environ)
            environment.update(self._environment(root))
            temporary = Path(environment["RUNNER_TEMP"])
            temporary.mkdir()
            target = root / "keep"
            target.mkdir()
            sentinel = target / "keep.txt"
            sentinel.write_text("untouched")
            try:
                (temporary / "the-daily-nexus").symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(CloudRuntimeError, "unlinked"):
                    cleanup_cloud_runtime(config_path=root / "config.toml.cloud")
            self.assertEqual("untouched", sentinel.read_text())

    def test_prepare_rejects_a_config_symlink_before_writing_credentials(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "keep.toml"
            target.write_text("untouched")
            config = root / "config.toml.cloud"
            try:
                config.symlink_to(target)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            environment = dict(os.environ)
            environment.update(self._environment(root))
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("audiodigest.cloud_runtime.Path.home", return_value=root / "home"),
            ):
                with self.assertRaisesRegex(CloudRuntimeError, "config.toml.cloud"):
                    prepare_cloud_runtime(template_path=Path("config.cloud.example.toml"),
                                          output_path=config)
            self.assertFalse((root / "runner-temp").exists())
            self.assertEqual("untouched", target.read_text())

    def test_cleanup_failure_is_reported_and_other_managed_credentials_are_removed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            home = root / "home"
            output = root / "config.toml.cloud"
            environment = dict(os.environ)
            environment.update(self._environment(root))
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("audiodigest.cloud_runtime.Path.home", return_value=home),
            ):
                prepare_cloud_runtime(template_path=Path("config.cloud.example.toml"),
                                      output_path=output)
                with patch(
                    "audiodigest.cloud_runtime.shutil.rmtree", side_effect=OSError("failure"),
                ):
                    with self.assertRaisesRegex(CloudRuntimeError, "incomplete"):
                        cleanup_cloud_runtime(config_path=output)
                self.assertFalse(output.exists())
                self.assertFalse((home / ".gemini" / "settings.json").exists())
                cleanup_cloud_runtime(config_path=output)

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
