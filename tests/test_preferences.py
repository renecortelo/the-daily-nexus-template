import tempfile
import tomllib
from pathlib import Path
from unittest import TestCase

from audiodigest.config import load_settings
from audiodigest.preferences import (
    PreferenceValidationError,
    controls_for_voice_id,
    save_preferences,
    validate_gmail_label,
    voice_id_for_controls,
)


class PreferenceTests(TestCase):
    def test_nested_gmail_label_is_accepted(self):
        self.assertEqual(
            "AudioDigest/Source",
            validate_gmail_label("  AudioDigest/Source  "),
        )

    def test_empty_or_malformed_gmail_label_is_rejected(self):
        for value in ("", "/AudioDigest", "AudioDigest/", "AudioDigest//Source"):
            with self.subTest(value=value):
                with self.assertRaises(PreferenceValidationError):
                    validate_gmail_label(value)

    def test_preferences_are_saved_and_loadable(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            config_path = Path(name) / "config.toml"
            config_path.write_text(
                """
[app]
gmail_label = "AudioDigest/Source"

[antigravity]
use_g1_credits = false
telemetry = false

[audio]
voice = "am_michael"
language_code = "a"

[podcast]
tone = "dry_wit"
""".strip(),
                encoding="utf-8",
            )

            save_preferences(
                config_path,
                gmail_label="Briefings/AI",
                voice_id="bf_emma",
                tone_id="formal",
                solo_name="Nox",
            )

            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("Briefings/AI", raw["app"]["gmail_label"])
            self.assertEqual("bf_emma", raw["audio"]["voice"])
            self.assertEqual("b", raw["audio"]["language_code"])
            self.assertEqual("formal", raw["podcast"]["tone"])
            self.assertEqual(1, raw["hosts"]["count"])
            self.assertEqual("Nox", raw["hosts"]["solo_name"])
            self.assertEqual("bf_emma", raw["hosts"]["primary_voice"])
            self.assertEqual("formal", raw["hosts"]["primary_tone"])
            self.assertEqual("manual", raw["firebase"]["publish_mode"])
            settings = load_settings(config_path)
            self.assertEqual("formal", settings.podcast.tone)
            self.assertEqual("bf_emma", settings.hosts.primary_voice)
            self.assertEqual(["Nox"], settings.hosts.active_names)

    def test_human_voice_controls_hide_engine_voice_names(self):
        self.assertEqual(
            "am_eric",
            voice_id_for_controls("Male", "Clear and analytical"),
        )
        self.assertEqual(
            ("Female", "Polished and expressive"),
            controls_for_voice_id("af_bella"),
        )
