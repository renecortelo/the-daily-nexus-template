import shutil
import tempfile
import tomllib
from pathlib import Path
from unittest import TestCase

from audiodigest.config import load_settings
from audiodigest.cost_guard import write_spark_confirmation
from audiodigest.preferences import PreferenceValidationError, _set_toml_string
from audiodigest.publishing_setup import (
    configure_private_publishing,
    enable_private_publishing,
)


class PublishingSetupTests(TestCase):
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
            self.assertGreaterEqual(len(firebase["secret_path"]), 32)
            self.assertFalse(firebase["publish_enabled"])
            self.assertEqual("manual", firebase["publish_mode"])

    def test_existing_private_secret_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = self._config(Path(name))
            first = configure_private_publishing(config_path, "daily-nexus-one")
            first_raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
            secret = first_raw["firebase"]["secret_path"]

            second = configure_private_publishing(config_path, "daily-nexus-two")
            second_raw = tomllib.loads(config_path.read_text(encoding="utf-8"))

            self.assertTrue(first.created_new_secret)
            self.assertFalse(second.created_new_secret)
            self.assertEqual(secret, second_raw["firebase"]["secret_path"])

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
