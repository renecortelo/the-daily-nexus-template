import shutil
import tempfile
import tomllib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from keyring.errors import KeyringError

from audiodigest.config import (
    FIREBASE_FEED_VAULT_SERVICE,
    firebase_secret_username,
    load_settings,
)
from audiodigest.cost_guard import write_spark_confirmation
from audiodigest.preferences import PreferenceValidationError, _set_toml_string
from audiodigest.publishing_setup import (
    configure_private_publishing,
    enable_private_publishing,
)


class PublishingSetupTests(TestCase):
    def setUp(self) -> None:
        self.vault: dict[tuple[str, str], str] = {}
        get_patcher = patch(
            "keyring.get_password",
            side_effect=lambda service, username: self.vault.get((service, username)),
        )
        set_patcher = patch(
            "keyring.set_password",
            side_effect=lambda service, username, value: self.vault.__setitem__(
                (service, username), value
            ),
        )
        self.keyring_get = get_patcher.start()
        self.keyring_set = set_patcher.start()
        self.addCleanup(get_patcher.stop)
        self.addCleanup(set_patcher.stop)

    def _config(self, root: Path) -> Path:
        config_path = root / "config.toml"
        value = Path("config.example.toml").read_text(encoding="utf-8")
        value = _set_toml_string(
            value,
            "app",
            "runtime_dir",
            str(root / "runtime"),
        )
        config_path.write_text(value, encoding="utf-8")
        shutil.copy2("firebase.json", root / "firebase.json")
        return config_path

    def test_project_configuration_creates_a_private_secret_but_stays_disabled(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))

            result = configure_private_publishing(
                config_path,
                "daily-nexus-private-123",
            )

            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
            firebase = raw["firebase"]
            self.assertEqual("daily-nexus-private-123", result.project_id)
            self.assertEqual(
                "https://daily-nexus-private-123.web.app",
                firebase["base_url"],
            )
            self.assertEqual("", firebase["secret_path"])
            self.assertEqual("keyring", firebase["secret_storage"])
            stored_secret = self.vault[
                (
                    FIREBASE_FEED_VAULT_SERVICE,
                    firebase_secret_username("daily-nexus-private-123"),
                )
            ]
            self.assertGreaterEqual(len(stored_secret), 32)
            self.assertNotIn(stored_secret, config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                stored_secret,
                load_settings(config_path).firebase.secret_path,
            )
            self.assertFalse(firebase["publish_enabled"])
            self.assertEqual("manual", firebase["publish_mode"])

    def test_existing_private_secret_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            first = configure_private_publishing(config_path, "daily-nexus-one")
            secret = self.vault[
                (
                    FIREBASE_FEED_VAULT_SERVICE,
                    firebase_secret_username("daily-nexus-one"),
                )
            ]

            second = configure_private_publishing(config_path, "daily-nexus-two")
            second_raw = tomllib.loads(config_path.read_text(encoding="utf-8"))

            self.assertTrue(first.created_new_secret)
            self.assertFalse(second.created_new_secret)
            self.assertEqual("", second_raw["firebase"]["secret_path"])
            self.assertEqual(
                secret,
                self.vault[
                    (
                        FIREBASE_FEED_VAULT_SERVICE,
                        firebase_secret_username("daily-nexus-two"),
                    )
                ],
            )

    def test_existing_plaintext_secret_is_migrated_out_of_config(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            legacy_secret = "b" * 32
            value = _set_toml_string(
                config_path.read_text(encoding="utf-8"),
                "firebase",
                "secret_path",
                legacy_secret,
            )
            config_path.write_text(value, encoding="utf-8")

            result = configure_private_publishing(
                config_path,
                "daily-nexus-private-123",
            )

            serialized = config_path.read_text(encoding="utf-8")
            self.assertFalse(result.created_new_secret)
            self.assertNotIn(legacy_secret, serialized)
            self.assertEqual(
                legacy_secret,
                load_settings(config_path).firebase.secret_path,
            )

    def test_credential_vault_failure_does_not_change_config(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            original = config_path.read_text(encoding="utf-8")
            self.keyring_set.side_effect = KeyringError("test backend unavailable")

            with self.assertRaisesRegex(
                PreferenceValidationError,
                "credential vault could not store",
            ):
                configure_private_publishing(
                    config_path,
                    "daily-nexus-private-123",
                )

            self.assertEqual(original, config_path.read_text(encoding="utf-8"))

    def test_missing_vault_entry_never_silently_rotates_feed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            configure_private_publishing(config_path, "daily-nexus-private-123")
            self.vault.clear()
            original = config_path.read_text(encoding="utf-8")

            with self.assertRaisesRegex(
                PreferenceValidationError,
                "missing from the operating system credential vault",
            ):
                configure_private_publishing(
                    config_path,
                    "daily-nexus-private-123",
                )

            self.assertEqual(original, config_path.read_text(encoding="utf-8"))
            self.assertEqual({}, self.vault)

    def test_explicit_rotation_replaces_vault_secret_without_writing_it_to_toml(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            configure_private_publishing(config_path, "daily-nexus-private-123")
            vault_key = (
                FIREBASE_FEED_VAULT_SERVICE,
                firebase_secret_username("daily-nexus-private-123"),
            )
            original_secret = self.vault[vault_key]

            result = configure_private_publishing(
                config_path,
                "daily-nexus-private-123",
                rotate_secret=True,
            )

            replacement = self.vault[vault_key]
            self.assertTrue(result.created_new_secret)
            self.assertNotEqual(original_secret, replacement)
            self.assertNotIn(replacement, config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                replacement,
                load_settings(config_path).firebase.secret_path,
            )

    def test_invalid_project_id_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            with self.assertRaises(PreferenceValidationError):
                configure_private_publishing(config_path, "Not A Project")

    def test_enable_requires_and_uses_bound_spark_confirmation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            configure_private_publishing(config_path, "daily-nexus-private-123")
            settings = load_settings(config_path)
            write_spark_confirmation(settings)

            enable_private_publishing(config_path)

            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(raw["firebase"]["publish_enabled"])

    def test_invalid_publish_host_is_rejected_before_enable_is_saved(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            configure_private_publishing(config_path, "daily-nexus-private-123")
            value = config_path.read_text(encoding="utf-8")
            value = _set_toml_string(
                value,
                "firebase",
                "base_url",
                "https://not-the-configured-project.web.app",
            )
            config_path.write_text(value, encoding="utf-8")
            write_spark_confirmation(load_settings(config_path))

            with self.assertRaisesRegex(ValueError, "standard HTTPS"):
                enable_private_publishing(config_path)

            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(raw["firebase"]["publish_enabled"])
